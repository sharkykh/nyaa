import math

import flask
from flask_paginate import Pagination

from itsdangerous import BadSignature, URLSafeSerializer
from wtforms.validators import ValidationError

from nyaa import app, db, forms, models
from nyaa.search import (DEFAULT_MAX_SEARCH_RESULT, DEFAULT_PER_PAGE, SERACH_PAGINATE_DISPLAY_MSG,
                         _generate_query_string, search_db, search_elastic)
from nyaa.utils import chain_get

bp = flask.Blueprint('users', __name__)


@bp.route('/user/<user_name>', methods=['GET', 'POST'])
def view_user(user_name):
    user = models.User.by_username(user_name)

    if not user:
        flask.abort(404)

    admin_form = None
    mass_action_form = None
    if flask.g.user and flask.g.user.is_moderator and flask.g.user.level > user.level:
        admin_form = forms.UserForm()
        mass_action_form = forms.UserTorrentMassAction(flask.request.form, user=flask.g.user)
        default, admin_form.user_class.choices = _create_user_class_choices(user)
        if flask.request.method == 'GET':
            admin_form.user_class.data = default

    url = flask.url_for('users.view_user', user_name=user.username)
    if flask.request.method == 'POST' and admin_form and admin_form.validate():
        selection = admin_form.user_class.data
        log = None
        if selection == 'regular':
            user.level = models.UserLevelType.REGULAR
            log = "[{}]({}) changed to regular user".format(user_name, url)
        elif selection == 'trusted':
            user.level = models.UserLevelType.TRUSTED
            log = "[{}]({}) changed to trusted user".format(user_name, url)
        elif selection == 'moderator':
            user.level = models.UserLevelType.MODERATOR
            log = "[{}]({}) changed to moderator user".format(user_name, url)
        elif selection == 'banned':
            user.status = models.UserStatusType.BANNED
            log = "[{}]({}) changed to banned user".format(user_name, url)

        adminlog = models.AdminLog(log=log, admin_id=flask.g.user.id)
        db.session.add(user)
        db.session.add(adminlog)
        db.session.commit()

        return flask.redirect(url)

    user_level = ['Regular', 'Trusted', 'Moderator', 'Administrator'][user.level]

    req_args = flask.request.args

    search_term = chain_get(req_args, 'q', 'term')

    sort_key = req_args.get('s')
    sort_order = req_args.get('o')

    category = chain_get(req_args, 'c', 'cats')
    quality_filter = chain_get(req_args, 'f', 'filter')

    page_number = chain_get(req_args, 'p', 'page', 'offset')
    try:
        page_number = max(1, int(page_number))
    except (ValueError, TypeError):
        page_number = 1

    results_per_page = app.config.get('RESULTS_PER_PAGE', DEFAULT_PER_PAGE)

    query_args = {
        'term': search_term or '',
        'user': user.id,
        'sort': sort_key or 'id',
        'order': sort_order or 'desc',
        'category': category or '0_0',
        'quality_filter': quality_filter or '0',
        'page': page_number,
        'rss': False,
        'per_page': results_per_page
    }

    if flask.g.user:
        query_args['logged_in_user'] = flask.g.user
        if flask.g.user.is_moderator:  # God mode
            query_args['admin'] = True
        if flask.g.user.id == user.id:
            mass_action_form = forms.UserTorrentMassAction(flask.request.form, user=flask.g.user)
            is_logged_in_user_account = True

    # Use elastic search for term searching
    rss_query_string = _generate_query_string(search_term, category, quality_filter, user_name)
    use_elastic = app.config.get('USE_ELASTIC_SEARCH')
    if use_elastic and search_term:
        query_args['term'] = search_term

        max_search_results = app.config.get('ES_MAX_SEARCH_RESULT', DEFAULT_MAX_SEARCH_RESULT)

        # Only allow up to (max_search_results / page) pages
        max_page = min(query_args['page'], int(math.ceil(max_search_results / results_per_page)))

        query_args['page'] = max_page
        query_args['max_search_results'] = max_search_results

        query_results = search_elastic(**query_args)

        max_results = min(max_search_results, query_results['hits']['total'])
        # change p= argument to whatever you change page_parameter to or pagination breaks
        pagination = Pagination(p=query_args['page'], per_page=results_per_page,
                                total=max_results, bs_version=3, page_parameter='p',
                                display_msg=SERACH_PAGINATE_DISPLAY_MSG)
        return flask.render_template('user.html',
                                     use_elastic=True,
                                     pagination=pagination,
                                     torrent_query=query_results,
                                     search=query_args,
                                     user=user,
                                     user_page=True,
                                     rss_filter=rss_query_string,
                                     level=user_level,
                                     admin_form=admin_form,
                                     mass_action_form=mass_action_form,
                                     is_current_user=is_logged_in_user_account)

    # Similar logic as home page
    else:
        if use_elastic:
            query_args['term'] = ''
        else:
            query_args['term'] = search_term or ''
        query = search_db(**query_args)
        return flask.render_template('user.html',
                                     use_elastic=False,
                                     torrent_query=query,
                                     search=query_args,
                                     user=user,
                                     user_page=True,
                                     rss_filter=rss_query_string,
                                     level=user_level,
                                     admin_form=admin_form,
                                     mass_action_form=mass_action_form,
                                     is_current_user=is_logged_in_user_account)


@bp.route('/user/<user_name>/torrents', methods=['POST'])
def update_torrents(user_name):
    selected_torrent_ids = flask.request.form.getlist('selected_torrents')
    form = forms.UserTorrentMassAction(
        flask.request.form,
        user=flask.g.user,
        selected_torrents=selected_torrent_ids)

    try:
        status = 'info'
        if form.validate(user=flask.g.user):
            result = form.apply_user_action()
            if result['ok'] is False:
                status = 'danger'

        flask.flash(flask.Markup(
            f"<strong>{result['message']}</strong>."), status)
    except ValidationError as err:
        flask.flash(flask.Markup(
            f"<strong>{str(err)}</strong>."), 'danger')

    return flask.redirect(flask.url_for('users.view_user', user_name=user_name))


@bp.route('/user/activate/<payload>')
def activate_user(payload):
    s = get_serializer()
    try:
        user_id = s.loads(payload)
    except BadSignature:
        flask.abort(404)

    user = models.User.by_id(user_id)

    if not user:
        flask.abort(404)

    user.status = models.UserStatusType.ACTIVE

    db.session.add(user)
    db.session.commit()

    return flask.redirect(flask.url_for('account.login'))


def _create_user_class_choices(user):
    choices = [('regular', 'Regular')]
    default = 'regular'
    if flask.g.user:
        if flask.g.user.is_moderator:
            choices.append(('trusted', 'Trusted'))
        if flask.g.user.is_superadmin:
            choices.append(('moderator', 'Moderator'))
            choices.append(('banned', 'Banned'))

        if user:
            if user.is_moderator:
                default = 'moderator'
            elif user.is_trusted:
                default = 'trusted'
            elif user.is_banned:
                default = 'banned'

    return default, choices


def get_serializer(secret_key=None):
    if secret_key is None:
        secret_key = app.secret_key
    return URLSafeSerializer(secret_key)


def get_activation_link(user):
    s = get_serializer()
    payload = s.dumps(user.id)
    return flask.url_for('users.activate_user', payload=payload, _external=True)
