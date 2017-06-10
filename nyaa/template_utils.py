import os.path
from datetime import datetime
from email.utils import formatdate

import flask
from werkzeug.urls import url_encode

from nyaa import app, models
from nyaa.utils import cached_function

bp = flask.Blueprint('template-utils', __name__)
_static_cache = {}  # For static_cachebuster


# ######################### TEMPLATE GLOBALS #########################

@bp.app_template_global()
def static_cachebuster(filename):
    """ Adds a ?t=<mtime> cachebuster to the given path, if the file exists.
        Results are cached in memory and persist until app restart! """
    # Instead of timestamps, we could use commit hashes (we already load it in __init__)
    # But that'd mean every static resource would get cache busted. This lets unchanged items
    # stay in the cache.

    if app.debug:
        # Do not bust cache on debug (helps debugging)
        return flask.url_for('static', filename=filename)

    # Get file mtime if not already cached.
    if filename not in _static_cache:
        file_path = os.path.join(app.static_folder, filename)
        file_mtime = None
        if os.path.exists(file_path):
            file_mtime = int(os.path.getmtime(file_path))

        _static_cache[filename] = file_mtime

    return flask.url_for('static', filename=filename, t=_static_cache[filename])


@bp.app_template_global()
def modify_query(**new_values):
    args = flask.request.args.copy()

    for key, value in new_values.items():
        args[key] = value

    return '{}?{}'.format(flask.request.path, url_encode(args))


@bp.app_template_global()
def filter_truthy(input_list):
    """ Jinja2 can't into list comprehension so this is for
        the search_results.html template """
    return [item for item in input_list if item]


@cached_function
@bp.app_template_global()
def get_category_id_map():
    """
    Reads database for categories and turns them into a dict with
    ids as keys and combined names and titles dict as the value, ala
    {'1_0': {'name': 'Anime', 'title': 'Anime'},
     '1_2': {'name': 'Anime - English-translated', 'title': 'Anime - English'},
     ...}
    """
    cat_id_map = {}
    for main_cat in models.MainCategory.query:
        cat_id_map[main_cat.id_as_string] = dict(
            name=main_cat.name,
            title=main_cat.title)
        for sub_cat in main_cat.sub_categories:
            cat_id_map[sub_cat.id_as_string] = dict(
                name=' - '.join((main_cat.name, sub_cat.name)),
                title=' - '.join((main_cat.title, sub_cat.title)))
    return cat_id_map


@bp.app_template_global()
def category_name(cat_id):
    """ Given a category id (eg. 1_2), returns a category name (eg. Anime - English-translated) """
    return get_category_id_map().get(cat_id, {}).get('name', '???')


# ######################### TEMPLATE FILTERS #########################

@bp.app_template_filter('utc_time')
def get_utc_timestamp(datetime_str):
    """ Returns a UTC POSIX timestamp, as seconds """
    UTC_EPOCH = datetime.utcfromtimestamp(0)
    return int((datetime.strptime(datetime_str, '%Y-%m-%dT%H:%M:%S') - UTC_EPOCH).total_seconds())


@bp.app_template_filter('display_time')
def get_display_time(datetime_str):
    return datetime.strptime(datetime_str, '%Y-%m-%dT%H:%M:%S').strftime('%Y-%m-%d %H:%M')


@bp.app_template_filter('rfc822')
def _jinja2_filter_rfc822(date, fmt=None):
    return formatdate(date.timestamp())


@bp.app_template_filter('rfc822_es')
def _jinja2_filter_rfc822_es(datestr, fmt=None):
    return formatdate(datetime.strptime(datestr, '%Y-%m-%dT%H:%M:%S').timestamp())


@bp.app_template_filter()
def timesince(dt, default='just now'):
    """
    Returns string representing "time since" e.g.
    3 minutes ago, 5 hours ago etc.
    Date and time (UTC) are returned if older than 1 day.
    """

    now = datetime.utcnow()
    diff = now - dt

    periods = (
        (diff.days, 'day', 'days'),
        (diff.seconds / 3600, 'hour', 'hours'),
        (diff.seconds / 60, 'minute', 'minutes'),
        (diff.seconds, 'second', 'seconds'),
    )

    if diff.days >= 1:
        return dt.strftime('%Y-%m-%d %H:%M UTC')
    else:
        for period, singular, plural in periods:

            if period >= 1:
                return '%d %s ago' % (period, singular if int(period) == 1 else plural)

    return default
