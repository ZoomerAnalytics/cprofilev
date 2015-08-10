#!/usr/bin/env python

import argparse
import bottle
import cProfile
import os
import pstats
import re
import sys
import threading

try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        # Python 3 compatibility.
        from io import StringIO


VERSION = '1.0.7'

__doc__ = """\
An easier way to use cProfile.

Outputs a simpler html view of profiled stats.
Able to show stats while the code is still running!

"""


STATS_TEMPLATE = """\
<html>
    <head>
        <title>{{ title }} | cProfile Results</title>
    </head>
    <body>
        <pre>{{ !stats }}</pre>

        % if callers:
            <h2>Called By:</h2>
            <pre>{{ !callers }}</pre>
        % end

        % if callees:
            <h2>Called:</h2>
            <pre>{{ !callees }}</pre>
        % end

        <h3>Restrictions applied to get stats listed above:</h3>
        % if restrictions:
            <ul>
            % for r in restrictions:
                <li><pre>{{ !r }}</pre></li>
            % end
            </ul>
        % else:
            None
        % end
    </body>
</html>"""


SORT_KEY = 'sort'
FUNC_NAME_KEY = 'func_name'
FUNC_LOC_KEY = 'func_loc'


class Stats(object):
    """Wrapper around pstats.Stats class."""

    IGNORE_FUNC_NAMES = ['function', '']
    DEFAULT_SORT_ARG = 'cumulative'
    SORT_ARGS = {
        'ncalls': 'calls',
        'tottime': 'time',
        'cumtime': 'cumulative',
        'filename': 'module',
        'lineno': 'nfl',
    }

    STATS_LINE_REGEX = r'(.*)\((.*)\)$'
    HEADER_LINE_REGEX = r'ncalls|tottime|cumtime'
    FUNCTION_SIG_HEADER = 'filename:lineno(function)'

    def __init__(self, profile_output=None, profile_obj=None):
        self.profile = profile_output or profile_obj
        self.stream = StringIO()
        self.stats = pstats.Stats(self.profile, stream=self.stream)

    def read_stream(self):
        value = self.stream.getvalue()
        self.stream.seek(0)
        self.stream.truncate()
        return value

    def read(self):
        output = self.read_stream()
        lines = output.splitlines(True)
        info = {}
        for i in range(len(lines)):
            lines[i] = self.process_line(lines[i], info)
        return "".join(lines)

    @classmethod
    def process_line(cls, line, info):
        # Format header lines (such that clicking on a column header sorts by
        # that column).
        if re.search(cls.HEADER_LINE_REGEX, line):

            # Find and store the index of the function signature column
            if 'func_col_pos' not in info:
                match = line.find(cls.FUNCTION_SIG_HEADER)
                if match >= 0:
                    info['func_col_pos'] = match

            # Replace sort keys with links
            for key, val in cls.SORT_ARGS.items():
                url_link = bottle.template(
                    "<a href='{{ url }}'>{{ key }}</a>",
                    url=cls.get_updated_href({SORT_KEY: val}),
                    key=key)
                line = line.replace(key, url_link)

        # Format stat lines (such that clicking on the function name drills into
        # the function call).
        match = re.search(cls.STATS_LINE_REGEX, line)
        if match:
            prefix = match.group(1)
            func_name = match.group(2)
            if func_name not in cls.IGNORE_FUNC_NAMES:
                if 'func_col_pos' in info:
                    func_loc = prefix[info['func_col_pos']:]
                    prefix = prefix[:info['func_col_pos']]
                    full_url_link = bottle.template(
                        "<a href='{{ url }}'>{{ func_loc }}</a>",
                        url=cls.get_updated_href({FUNC_LOC_KEY: func_loc, FUNC_NAME_KEY: func_name}),
                        func_loc=func_loc)
                else:
                    full_url_link = ''
                url_link = bottle.template(
                    "<a href='{{ url }}'>{{ func_name }}</a>",
                    url=cls.get_updated_href({FUNC_LOC_KEY: None, FUNC_NAME_KEY: func_name}),
                    func_name=func_name)
                line = bottle.template(
                    "{{ prefix }}{{ !full_url_link }}({{ !url_link }})\n",
                    prefix=prefix, full_url_link=full_url_link, url_link=url_link)
        return line

    @classmethod
    def get_updated_href(cls, keyvals):
        href = '?'
        query = dict(bottle.request.query)
        for key, val in keyvals.items():
            query[key] = val
        for key in query.keys():
            if query[key] is not None:
                href += '%s=%s&' % (key, query[key])
        return href[:-1]

    def show(self, *restrictions):
        self.stats.print_stats(*restrictions)
        return self

    def show_callers(self, *restriction):
        self.stats.print_callers(*restriction)
        return self

    def show_callees(self, *restriction):
        self.stats.print_callees(*restriction)
        return self

    def sort(self, sort=''):
        sort = sort or self.DEFAULT_SORT_ARG
        self.stats.sort_stats(sort)
        return self


class CProfileV(object):
    def __init__(self, profile, title, address='127.0.0.1', port=4000):
        self.profile = profile
        self.title = title
        self.port = port
        self.address = address

        # Bottle webserver.
        self.app = bottle.Bottle()
        self.app.route('/')(self.route_handler)

    def route_handler(self):
        self.stats = Stats(self.profile)

        func_name = bottle.request.query.get(FUNC_NAME_KEY) or ''
        func_loc = bottle.request.query.get(FUNC_LOC_KEY) or ''
        sort = bottle.request.query.get(SORT_KEY) or ''

        restrictions = []
        if func_name:
            restrictions.append(r"\(" + re.escape(func_name) + r"\)")
        if func_loc:
            restrictions.append("^" + re.escape(func_loc))

        self.stats.sort(sort)
        callers = self.stats.show_callers(*restrictions).read() if func_name else ''
        callees = self.stats.show_callees(*restrictions).read() if func_name else ''
        data = {
            'title': self.title,
            'stats': self.stats.sort(sort).show(*restrictions).read(),
            'callers': callers,
            'callees': callees,
            'restrictions': restrictions
        }
        return bottle.template(STATS_TEMPLATE, **data)

    def start(self):
        self.app.run(host=self.address, port=self.port, quiet=True)


def main():
    parser = argparse.ArgumentParser(
        description='An easier way to use cProfile.',
        usage='%(prog)s [--version] [-a ADDRESS] [-p PORT] scriptfile [arg] ...',
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('-a', '--address', type=str, default='127.0.0.1',
        help='The address to listen on. (defaults to 127.0.0.1).')
    parser.add_argument('-p', '--port', type=int, default=4000,
        help='The port to listen on. (defaults to 4000).')
    # Preserve v0 functionality using a flag.
    parser.add_argument('-f', '--file', type=str,
        help='cProfile output to view.\nIf specified, the scriptfile provided will be ignored.')
    parser.add_argument('remainder', nargs=argparse.REMAINDER,
        help='The python script file to run and profile.',
        metavar="scriptfile")

    args = parser.parse_args()
    if not sys.argv[1:]:
        parser.print_help()
        sys.exit(2)

    info = '[cProfileV]: cProfile output available at http://%s:%s' % \
        (args.address, args.port)

    # v0 mode: Render profile output.
    if args.file:
        # Note: The info message is sent to stderr to keep stdout clean in case
        # the profiled script writes some output to stdout
        sys.stderr.write(info + "\n")
        cprofilev = CProfileV(args.file, title=args.file, address=args.address, port=args.port)
        cprofilev.start()
        return

    # v1 mode: Start script and render profile output.
    sys.argv[:] = args.remainder
    if len(args.remainder) < 0:
        parser.print_help()
        sys.exit(2)
        
    # Note: The info message is sent to stderr to keep stdout clean in case
    # the profiled script writes some output to stdout
    sys.stderr.write(info + "\n")
    profile = cProfile.Profile()
    progname = args.remainder[0]
    sys.path.insert(0, os.path.dirname(progname))
    with open(progname, 'rb') as fp:
        code = compile(fp.read(), progname, 'exec')
    globs = {
        '__file__': progname,
        '__name__': '__main__',
        '__package__': None,
    }

    # Start the given program in a separate thread.
    progthread = threading.Thread(target=profile.runctx, args=(code, globs, None))
    progthread.setDaemon(True)
    progthread.start()

    cprofilev = CProfileV(profile, title=progname, address=args.address, port=args.port)
    cprofilev.start()


if __name__ == '__main__':
    main()
