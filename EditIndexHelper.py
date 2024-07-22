import argparse
import csv
import json
import logging
import os
import platform
import pprint
import re
import subprocess
import sys

"""
Edit Index Helper

This tool can read Edit Index csv files from Davinci Resolve.
It can also find media files like Quicktime, and use ffprobe to read their metadata.

Currently, the only functionality is to match media files by name to csv lines, and create EDL(s) from matches.

How to use it
Use command line arguments for basics:
-i to set root folder for Edit Index csv file(s).
-m to set root folder for searching media files.
-p to set custom path for prefs.json. See json _help items for more details
Without any arguments, the above -i and -m options will be read from prefs.json file ath the main script location.

Functionality
(see prefs.json)

1. Search for edit index csv files
1.1 You can set filter_include, filter_exclude, pattern to limit to certain file names only. Check_required_columns allows for forcing to only accept valid csv files.
1.2 The CSV grouping allows to filter csv file names, and only read the file that is last in alphabetic sort. For example edit15_v01.csv, edit15_v02.csv can be set to only accept v02.
1.3 The match skip options allows to add arbitrary number of filters to skip lines in csv file.

2. Search for media files
2.1 You can set filter_include, filter_exclude, pattern to limit to certain file names only.
2.2 Media_meta options set paths to external tools for reading metadata.

3. Matching media files to csv lines
3.1 Csv matching column, csv_pattern, csv_repl allow to run regex search and replace on csv column of choice
3.2 Csv matching media, media_pattern, media_repl allow to run regex search and replace on media file name
3.3 If the regex result of 3.1 and 3.2 are matching, the media file and corresponding csv lines are merged

4. Exporting matching media files to EDL
4.1 The EDL options control edl path and file name, plus frame rate options. Max reel option trims the reel to the number of characters specified.
4.2 Edl reel options allow to run regex search and replace on source. Result is used for reel in the edl
4.3 Edl clip options allow to run regex search and replace on source. Result is used for additional remark line
4.4 Edl clip path options allow to run regex search and replace on source. Result is used for additional remark line.


Possible future updates can do:
Find "longest take" from multiple edit index files for load / scan edl.
Use edit index files together with marker exports from Davinci Resolve to assign vfx names to clips.  
"""

if getattr(sys, 'frozen', False):
    script_path = str(os.path.dirname(sys.executable).replace("\\", "/"))
else:
    script_path = str(os.path.dirname(__file__).replace("\\", "/"))

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter_time = logging.Formatter('%(asctime)s:%(levelname)s:%(message)s')
formatter = logging.Formatter('%(levelname)s:%(message)s')
file_handler = logging.FileHandler(script_path + '/edit-tool.log')
file_handler.setFormatter(formatter_time)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)


class EditToolException(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class EditTool:
    def __init__(self, prefs, script_path):
        self.prefs = prefs
        self.script_path = script_path
        self.csvs = {}
        self.csv_groups = {}
        self.media = {}
        self.edls = {}

    def regex_test(self, source, tokens, pattern, repl=''):
        """ takes source string, maps tokens,
         and uses regex pattern

        :return:
        first found group, or replace if repl is present
        """

        class Default(dict):
            def __missing__(self, key):
                return '{' + key + '}'

        regex_valid = False
        compiled = None
        try:
            compiled = re.compile(pattern)
            regex_valid = True
        except re.error as e:
            logger.error("Regex pattern {}\n{}".format(pattern, e))

        filled_source = source.format_map(Default(tokens))
        result = ''
        if regex_valid:
            if repl and repl != '':
                try:
                    result = re.sub(compiled, repl, filled_source)
                except:
                    pass
            else:
                m = compiled.search(filled_source)
                if m:
                    try:
                        result = m.group(1)
                    except:
                        pass
        return result

    def tc_to_frames(self, tc, fps_float):

        def _seconds(value, fr_int):
            _zip_ft = zip((3600, 60, 1, 1 / fr_int), value.split(':'))
            return sum(f * float(t) for f, t in _zip_ft)

        def _frames(secs, fr_int):
            return secs * fr_int

        fr_int = int(round(fps_float))
        return round(_frames(_seconds(tc, fr_int), fr_int))

    def frames_to_tc(self, frames, fps_float):

        fps = int(round(fps_float))
        h = int(frames / (3600 * fps))
        m = int(frames / (60 * fps)) % 60
        s = int((frames % (60 * fps)) / fps)
        f = frames % (60 * fps) % fps
        return "{:02d}:{:02d}:{:02d}:{:02d}".format(h, m, s, f)

    def parse_file_name(self, file_name):
        r"""
        Parses file path

        Replaces backslashes with slashes to normalise windows schizofreny
        For unc paths like //host/mount/dir/fname.ext, parts will return '//' as first part
        If path starts with drive letter, it is separated to seq.drive
        If filename ends with slash, last part will be empty string

        {'unc_host': '//server/directory', 'clean_name_no_sep': 'filename', 'number': 1234, 'padding': 4,
        'clean_name_sep_char': '.', 'path': '//server/directory', 'clean_name': 'filename.', 'number_string': '1234',
        'name': 'filename.1234', 'extension': 'ext', 'drive': '', 'parts': ['//', 'server', 'directory',
        'filename.1234.ext'], 'full_path': '//server/directory/filename.1234.ext'}
        """

        # Replaces backslashes with slashes to normalise windows schizophrenia
        fn = file_name.replace('\\', '/')
        try:
            filename = fn.decode("utf-8")
        except:
            filename = fn

        seq = dict(full_path=filename, drive='', unc_host='', path='', name='', extension='', clean_name='',
                   number_string='', number=0, padding=0, clean_name_no_sep='', clean_name_sep_char='', parts=[],
                   pattern_hash_only='', after_number='')

        # detects UNC and drive letters
        # seq['unc_host'], path = os.path.splitunc(filename)
        seq['drive'], path = os.path.splitdrive(filename)

        # split path to directories and filename
        seq['parts'] = []
        while True:
            newpath, tail = os.path.split(path)
            if newpath == path:
                assert not tail
                if path: seq['parts'].append(path)
                break
            seq['parts'].append(tail)
            path = newpath
        seq['parts'].reverse()

        seq['path'] = os.path.dirname(filename)
        seq['name'] = os.path.basename(filename)

        if seq['path'] is None:
            seq['path'] = ''
            seq['name'] = seq["full_path"]

        seq['name'], seq['extension'] = os.path.splitext(seq['name'])
        if seq["extension"] is None:
            seq["extension"] = ''
        else:
            seq["extension"] = seq["extension"][1:]

        # this regex requires dot before counter, allows for after counter chars "bla.1001.crypto"
        NM_RE = re.compile(r"(?P<clean>.+)(?P<sep>\.)(?P<counter>[0-9]+)(?P<after>.*)")
        m = NM_RE.search(seq["name"])
        if m is not None:
            seq["number_string"] = m.group("counter")
            seq["pattern_hash_only"] = len(m.group("counter")) * '#'
            seq["padding"] = len(seq["number_string"])
            seq["number"] = int(seq["number_string"])

            if m.group("clean") and m.group("sep"):
                seq["clean_name"] = m.group("clean") + m.group("sep")
            if m.group("sep") and m.group("sep") in ['.', '_', '-']:
                seq["clean_name_no_sep"] = m.group("clean")
                seq["clean_name_sep_char"] = m.group("sep")
            if m.group("after"):
                seq["after_number"] = m.group("after")
        else:
            seq["clean_name"] = seq["name"]
            seq["clean_name_no_sep"] = seq["name"]

        return seq

    def get_metadata(self, full_path):
        """
        reads metadata by ffprobe
        """
        multi_frame_ext = ['mov', 'avi', 'mpg', 'mpeg', 'mp2', 'mpv',
                           'mp4', 'm4v', 'gov', 'qt', 'r3d', 'mxf']
        image_ext = ['dpx', 'cin', 'jpg', 'jpeg', 'tif', 'tiff', 'rgb',
                     'sgi', 'tga', 'png', 'exr', 'dng']

        def get_category(full_path):

            category = 'unknown'
            file_ext = str.lower(os.path.splitext(full_path)[1])
            if file_ext is None or len(file_ext) == 0:
                return category
            file_ext = file_ext[1:]
            if file_ext in image_ext:
                #TODO sequence
                category = 'still'
            elif file_ext in multi_frame_ext:
                category = 'video'

            return category

        def get_ffprobe_data(path_to_file):
            """Load data about entered filepath via ffprobe.

            Args:
                path_to_file (str): absolute path
            """

            ffprobe = self.prefs['media_meta']['ffprobe_path']
            if ffprobe is None:
                return None

            ff_args = [ffprobe] + [
                "-hide_banner",
                "-loglevel", "fatal",
                "-show_error",
                "-show_format",
                "-show_streams",
                "-show_programs",
                "-show_chapters",
                "-show_private_data",
                "-print_format", "json",
                path_to_file
            ]
            kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if platform.system().lower() == "windows":
                kwargs["creationflags"] = (
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        | getattr(subprocess, "DETACHED_PROCESS", 0)
                        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            popen = subprocess.Popen(ff_args, **kwargs)
            popen_stdout, popen_stderr = popen.communicate()
            if popen_stdout:
                pass
            if popen_stderr:
                pass
            return json.loads(popen_stdout)

        def seconds_to_frames(seconds, fps):

            seconds = float(seconds)
            try:
                fps = float(fps)
            except:
                if '/' in fps:
                    a = float(fps.split('/')[0])
                    b = float(fps.split('/')[1])
                    fps = float(a) / float(b)
                else:
                    fps = None

            if fps:
                total_frames = round(fps * seconds)
            else:
                total_frames = 0

            return str(total_frames)

        metadata = {}
        input_file_metadata = get_ffprobe_data(full_path)
        _s = input_file_metadata.get('streams')
        tc = None
        if _s:
            stream = next((s for s in _s if s.get("codec_type") == "video"), {})
            if stream:
                metadata = {
                    'width': int(stream.get("width", '0')),
                    'height': int(stream.get("height", '0')),
                    'pa': str(stream.get("sample_aspect_ratio", "")),  # "1:1"
                    'fps_raw': str(stream.get("r_frame_rate", '0')),  # "24/1"
                    'fps': float(eval(str(stream.get("r_frame_rate", '0')))), # 24.0
                    'duration_frames': int(stream.get("nb_frames", '0')),
                    'duration_secs': float(stream.get("duration", '0.0')), # "38.333333" seconds
                    'category': get_category(full_path),
                    'timecode': None,
                    'tc_in_frames': None,
                    'tc_out': None,
                    'tc_out_frames': None
                }

            stream_tc = next((s for s in _s if s.get("codec_tag_string") == "tmcd"), {})
            if stream_tc:
                _t = stream_tc.get('tags')
                if _t:
                    tc = _t.get('timecode')
            metadata['timecode'] = tc

            # calculate frame duration
            ds = metadata.get('duration_secs')
            fps = metadata.get('fps')
            if ds is not None and fps is not None:
                metadata['duration_frames'] = seconds_to_frames(seconds=float(ds), fps=float(fps))
            else:
                logger.error("get_metadata: failed to calculate frame duration. Duration in seconds: {} fps: {}.".format(ds, fps))

            # calculate stc in stc out
            if metadata['duration_frames'] is not None and metadata['duration_frames'] != 0 and metadata['timecode'] is not None:
                metadata['tc_in_frames'] = int(self.tc_to_frames(metadata['timecode'], fps))
                metadata['tc_out_frames'] = metadata['tc_in_frames'] + int(metadata['duration_frames']) - 1
                metadata['tc_out'] = str(self.frames_to_tc(metadata['tc_out_frames'], fps))

        return metadata

    def get_file_list(self, root, include=None, exclude=None, pattern=None, recursive=True):

        if not os.path.isdir(root):
            raise EditToolException("Folder path unreachable: {}".format(root))

        file_list = []
        files = []
        if recursive:
            files = [os.path.join(dirpath, f) for (dirpath, dirnames, filenames) in os.walk(root) for f in filenames]
        else:
            files = os.listdir(root)
            files = [root + '/' + f for f in files if os.path.isfile(root + '/' + f)]

        if files and len(files) > 0:
            for one_file in files:
                if include != '' and include not in one_file:
                    #print(f"Skip file {one_file} due to include filter.")
                    continue
                if exclude != '' and exclude in one_file:
                    #print(f"Skip file {one_file} due to exclude filter.")
                    continue
                if pattern != '':
                    if not bool(re.match(pattern, os.path.basename(one_file))):
                        logger.debug(f"Skip file {os.path.basename(one_file)} due to pattern filter.")
                        continue
                file_list.append(one_file)
        else:
            logger.warning("No files found at {}".format(root))
        return file_list

    def _group_csvs(self, csvs):
        """Assign every csv in one or more group.
        if prefs['group_csv']['highest_only'], only take the highest csv from the group
        This allows for versioned csv files to be kept in one place, but only highest "version" will be used

        self.csvs_groups is a dict
        key is the group name
        value is a sorted list of dicts where each dict contains: 'csv_path' 'csv_name' 'group': 'sort'

        returns copy of csvs, with "highest versions" only, if applicable
        """

        common_valid = False
        try:
            common_compiled = re.compile(self.prefs['group_csv']['common'])
            common_valid = True
        except re.error as e:
            logger.error("Regex pattern error: {}\n{}".format(self.prefs['group_csv']['common'], e))
        sort_valid = False
        try:
            sort_compiled = re.compile(self.prefs['group_csv']['sort'])
            sort_valid = True
        except re.error as e:
            logger.error("Regex pattern error: {}\n{}".format(self.prefs['group_csv']['sort'], e))

        sort_me = {}
        if common_valid and sort_valid:
            for csv_path, one_csv in csvs.items():
                name = os.path.basename(csv_path)
                m_c = common_compiled.search(name)
                result_common = None
                result_sort = None
                if m_c:
                    try:
                        result_common = m_c.group(1)
                    except:
                        pass
                m_s = sort_compiled.search(name)
                if m_s:
                    try:
                        result_sort = m_s.group(1)
                    except:
                        pass
                one_rec = {'csv_path': csv_path, 'csv_name': name, 'group': result_common, 'sort': result_sort}
                if result_common is not None and sort_me.get(result_common) is not None:
                    sort_me[result_common].append(one_rec)
                else:
                    sort_me[result_common] = [one_rec]
        else:
            logger.error("Grouping regexes not valid. No grouping done.")
            return csvs

        # sort grouped csvs
        sort_me_sorted = {}
        grouped_csvs = {}
        for group, csv_list in sort_me.items():
            sort_me_sorted[group] = sorted(csv_list, key=lambda d: d['sort'])
            if self.prefs['group_csv']['highest_only']:
                last = sort_me_sorted[group][-1]
                grouped_csvs[last['csv_path']] = csvs[last['csv_path']]
            else:
                for one_csv in sort_me_sorted[group]:
                    # store csv content in new dict
                    grouped_csvs[one_csv['csv_path']] = csvs[one_csv['csv_path']]

        self.csvs_groups = sort_me_sorted
        return grouped_csvs

    def read_csvs(self):
        """ Reads csvs from disc

        :return:
        Dict where key is full path to csv file and
        value is the csv contents as a list of dicts
        """

        def is_match_skip(csv_line):
            filter_matches = 0
            for one_filter in self.prefs['csv_match_skip']:
                reg_test = self.regex_test(csv_line.get(one_filter['column']), {}, one_filter['pattern'], one_filter['repl'])
                if reg_test and reg_test != '':
                    if one_filter['invert']:
                        if one_filter['equals'] != '' and one_filter['equals'] != reg_test:
                            filter_matches += 1
                    else:
                        if one_filter['equals'] != '' and one_filter['equals'] == reg_test:
                            filter_matches += 1

            if filter_matches > 0:
                return True
            else:
                return False
        def csv_tc_to_frames(one_line) -> dict:

            fps = float(self.prefs['edl']['frame_rate'])
            out_line = {}
            tc_columns = ["csv_sin", "csv_sout", "csv_rin", "csv_rout"]
            for k, v in one_line.items():
                out_line[k] = v
                if k in tc_columns:
                    name = str(k) + '_frames'
                    if v and v != '':
                        frames = self.tc_to_frames(v, fps)
                    else:
                        frames = None
                    out_line[name] = frames
            return out_line

        def rename_columns(one_line) -> dict:

            rename = self.prefs['csv_columns']['rename']
            target_names = list(rename.keys())
            csv_names = list(rename.values())
            out_line = {}
            for k, v in one_line.items():
                if k in csv_names:
                    out_line[target_names[csv_names.index(k)]] = v
                else:
                    out_line[k] = v
                    #one_line[target_names[csv_names.index(k)]] = one_line.pop(k)

            return out_line

        csv_files = self.get_file_list(
            root=self.prefs['search_csv']['root_folder'],
            include=self.prefs['search_csv']['filter_include'],
            exclude=self.prefs['search_csv']['filter_exclude'],
            pattern=self.prefs['search_csv']['pattern'],
            recursive=False)

        csvs = {}
        if csv_files is None or len(csv_files) == 0:
            raise EditToolException(f"No csv files found at {self.prefs['search_csv']['root_folder']}")

        required_columns = self.prefs['csv_columns']['required']
        for one_csv in csv_files:
            one_csv = one_csv.replace('\\', '/')
            csv_listdict = []
            valid_lines = 0
            if one_csv and one_csv != '' and os.path.exists(one_csv):
                try:
                    with open(one_csv) as csvfile:
                        reader = csv.DictReader(csvfile)
                        for row in reader:
                            line_dict = dict(row)
                            line_dict = rename_columns(line_dict)
                            line_dict = csv_tc_to_frames(line_dict)
                            line_dict['csv_key'] = '' # for matching
                            if is_match_skip(line_dict):
                                line_dict['csv_skip_line'] = True
                            else:
                                line_dict['csv_skip_line'] = False
                                valid_lines += 1
                            csv_listdict.append(line_dict)
                except IOError:
                    logger.error('Error opening csv file {}'.format(one_csv))
            skip = True
            if csv_listdict and len(csv_listdict) > 0:
                if valid_lines == 0:
                    logger.warning(f"Csv file {os.path.basename(one_csv)} has no valid lines. Skipping file.")
                else:
                    skip = False
                    columns = list(csv_listdict[0].keys())
                    if self.prefs['search_csv']['check_required_columns']:
                        if not set(required_columns).issubset(columns):
                            skip = True
                            logger.warning(f"Csv file {os.path.basename(one_csv)} is missing required column(s). Skipping file.")
            if not skip:
                logger.debug(f"Adding csv file {os.path.basename(one_csv)} with {valid_lines} valid lines.")
                csvs[one_csv] = csv_listdict

        if csvs is None or csvs == {}:
            raise EditToolException(f"No readable csv files found at {self.prefs['search_csv']['root_folder']}")
        else:
            logger.info(f"read_csvs -> Found {len(list(csvs.keys()))} Csv file(s).")


        self.csvs = self._group_csvs(csvs)
        grouped_csvs_count = len(list(self.csvs.keys()))
        logger.info(f"read_csvs -> Found {grouped_csvs_count} Csv file(s) after grouping.")

        cnt = 0
        for one_csv, lst in self.csvs.items():
            cnt +=1
            logger.info(f"read_csvs -> {cnt} Csv file :{os.path.basename(one_csv)}")

    def find_media(self):
        """ Get media data

        key is the csv_matching media, media_pattern, media_repl result
        value is a dict
            'file': full path of the media
            'metadata': ffprobe metadata
            'csv': the matching csv line
        ""

        """

        self.media = {}
        media_files = self.get_file_list(
            root=self.prefs['search_media']['root_folder'],
            include=self.prefs['search_media']['filter_include'],
            exclude=self.prefs['search_media']['filter_exclude'],
            pattern=self.prefs['search_media']['pattern'],
            recursive=False)

        if media_files is None or len(media_files) == 0:
            raise EditToolException("Folder {} has no media files.".format(self.prefs['search_media']['root_folder']))

        try:
            _ = re.compile(self.prefs['csv_matching']['media_pattern'])
        except re.error as e:
            logger.error("CSV matching media pattern regex error:\n{}".format(e))
            return None

        for one_file in media_files:
            result = self.regex_test(self.prefs['csv_matching']['media'],
                                     self.parse_file_name(one_file),
                                     self.prefs['csv_matching']['media_pattern'],
                                     self.prefs['csv_matching']['media_repl'])
            if result is not None and result != '':
                meta = self.get_metadata(one_file)
                if meta is None or meta == {}:
                    meta = {}
                    logger.error(f"Failed to read metadata from file {one_file}")
                self.media[result] = {'file': one_file, 'metadata': meta}
            else:
                logger.error(f"find_media -> Failed to get regex result for {os.path.basename(one_file)}")

        if self.media is not None and self.media != {}:
            logger.info(f"find_media -> Found {len(list(self.media.keys()))} media file(s).")

        return self.media

    def prep_matching(self):
        """
        Runs regex on every csv line
        """

        # reset matching
        for csv_file, one_csv in self.csvs.items():
            for one_line in one_csv:
                one_line['csv_key'] = ''

        for csv_file, one_csv in self.csvs.items():
            cnt = 0
            for one_line in one_csv:
                cnt += 1

                # reset matching
                one_line['csv_key'] = ''

                # skip line
                if one_line['csv_skip_line']:
                    continue
                result = self.regex_test(one_line.get(self.prefs['csv_matching']['column']), {},
                                         self.prefs['csv_matching']['csv_pattern'],
                                         self.prefs['csv_matching']['csv_repl'])
                if result is not None and result != '':
                    one_line['csv_key'] = result

        return self.csvs

    def csv_matching(self):
        """ CSV matching the media files

        :return:
        """
        def is_tc_matching(csv_in, media_in, csv_out, media_out):
            result = False
            if csv_in is None or media_in is None or csv_out is None or media_out is None:
                return result
            if csv_in >= media_in and csv_out <= media_out:
                result = True
            return result

        match_tc = self.prefs['csv_matching'].get('match_timecode', False)
        if self.media is None or self.media == {}:
            raise EditToolException("No suitable media files found for csv matching.")
        if self.csvs is None or self.csvs == {}:
            raise EditToolException("No suitable csv files found for matching.")

        matched_media_counter = 0
        media_not_matched = []
        for media_key, media_dict in self.media.items():
            found = False
            for csv_file, one_csv in self.csvs.items():
                cnt = 0
                for one_line in one_csv:
                    cnt += 1
                    # check if skip line due to skip filters
                    if one_line['csv_skip_line']:
                        continue
                    if one_line['csv_key'] == media_key:
                        if match_tc:
                            tc_ok = is_tc_matching(one_line['csv_sin_frames'], media_dict['metadata']['tc_in_frames'], one_line['csv_sout_frames'], media_dict['metadata']['tc_out_frames'])
                        else:
                            tc_ok = True
                        if tc_ok:
                            media_dict['csv_line'] = one_line
                            media_dict['csv_file'] = csv_file
                            one_line['csv_matched_media'] = media_dict['file']
                            found = True
                            matched_media_counter += 1
                            logger.debug(f"csv_matching -> Found matching csv line for {os.path.basename(media_dict['file'])} at {os.path.basename(csv_file)} line {cnt}")
                            # first found media is enough
                            break
                        else:
                            if match_tc:
                                logger.debug(f"csv_matching -> Csv line for {os.path.basename(media_dict['file'])} at {os.path.basename(csv_file)} line {cnt} not matching timecode.")
                if found:
                    # first found media is enough
                    break
            if not found:
                logger.debug(f"csv_matching -> Matching csv line for the media file {media_key} not found.")
                media_not_matched.append(media_dict['file'])

        logger.info(f"csv_matching -> Matched {matched_media_counter}  from {len(list(self.media.keys()))} media files")
        if len(media_not_matched) > 0:
            logger.warning(f"csv_matching -> {len(media_not_matched)} media not matched to csv")
            logger.info(f"{pprint.pformat(media_not_matched)}")

    def matched_media_to_edls(self):

        def media_to_edl_line(one_media, line_number):

            number = str(line_number).zfill(3) + '  '
            vc = 'V     C        '
            tcs = one_media['csv_line']['csv_sin'] + ' ' + one_media['csv_line']['csv_sout'] + ' ' + \
                  one_media['csv_line']['csv_rin'] + ' ' + one_media['csv_line']['csv_rout']

            tokens = dict(one_media['csv_line'])
            tokens.update(one_media['metadata'])
            tokens['media_path'] = one_media['file']
            tokens['media_file'] = os.path.basename(one_media['file'])
            tokens['media_key'] = one_media['media_key']

            reel = self.regex_test(self.prefs['edl_reel']['source'], tokens, self.prefs['edl_reel']['pattern'],
                                   self.prefs['edl_reel']['repl'])
            if reel is None or reel == '':
                reel = 'AX'
            reel = reel + (' ' * (self.prefs['edl']['max_reel'] - len(reel)))
            reel += ' '

            fcm = self.regex_test(self.prefs['edl_clip']['source'], tokens, self.prefs['edl_clip']['pattern'],
                                   self.prefs['edl_clip']['repl'])

            fp = self.regex_test(self.prefs['edl_clip_path']['source'], tokens, self.prefs['edl_clip_path']['pattern'],
                                   self.prefs['edl_clip_path']['repl'])

            line = number + reel + vc + tcs + '\n'
            if self.prefs['edl_clip']['export']:
                line += fcm + '\n'
            if self.prefs['edl_clip_path']['export']:
                line += fp + '\n'

            return line

        # media has media_key: {csv_file, file, metadata, csv_line}
        # csv_file: path to csv
        # csv_line: dict with tc and more
        # file: path to mov ..
        # metadata: dict with tc, width, height, par, duration frames ...

        # rearrange to dict with key being the csv file, value list of dicts
        by_csv = {}
        for media_key, media_dict in self.media.items():
            my_media = media_dict
            my_media['media_key'] = media_key
            _f = media_dict.get('csv_file')
            _exists = by_csv.get(_f)
            if _exists and _f:
                by_csv[_f].append(my_media)
            else:
                by_csv[_f] = [my_media]

        if self.prefs['edl']['drop_frame']:
            fcm = 'FCM: DROP FRAME\n\n'
        else:
            fcm = 'FCM: NON-DROP FRAME\n\n'

        matched_media_count = 0
        for one_csv, medias in by_csv.items():
            if one_csv is None:
                continue
            sorted_medias = sorted(medias, key=lambda d: d.get('csv_line', {}).get('csv_rin_frames'))

            # edl folder:
            edl_root = self.prefs['edl']['custom_folder'].replace('\\', '/')
            first_media_path = os.path.dirname(sorted_medias[0]['file']).replace('\\', '/')
            if self.prefs['edl']['use_media_root']:
                edl_root = first_media_path
            elif self.prefs['edl']['use_media_root_up']:
                edl_root = '/'.join(first_media_path.split('/')[:-1])
            logger.debug(f"The EDL root is {edl_root}")

            # edl name:
            edl_name = self.prefs['edl']['edl_name_custom']
            if self.prefs['edl']['edl_name_from_csv']:
                edl_name = os.path.basename(one_csv)[:-4]
            elif self.prefs['edl']['edl_name_from_media_folder']:
                edl_name = first_media_path.split('/')[-1]
            edl_name = self.prefs['edl']['edl_name_prefix'] + edl_name + self.prefs['edl']['edl_name_suffix']
            logger.debug(f"The EDL root is {edl_name}")

            header = f"TITLE: {edl_name}\n" + fcm
            cnt = 0
            lines = header
            for one_media in sorted_medias:
                cnt += 1
                matched_media_count += 1
                lines += media_to_edl_line(one_media, cnt)

            edl_path = edl_root + '/' + edl_name + '.edl'
            self.edls[edl_path] = lines

        logger.info(f"Generated {len(self.edls.keys())} EDL(s), with {matched_media_count} media files.")
        for edl_file, edl_content in self.edls.items():
            try:
                os.makedirs(os.path.dirname(edl_file), exist_ok=True)
                with open(edl_file, 'w') as f:
                    f.write(edl_content)
                logger.info(f"Created edl {edl_file}.")
            except Exception as e:
                logger.error(f"Failed to write edl {edl_file}.\n{e}")


def main() -> None:

    def get_args():
        parser = argparse.ArgumentParser(
            description="Takes Edit Index from Davinci Resolve, and converts matching media to EDL.")
        parser.add_argument(
            '-i',
            help="Root folder for Edit Index csv file(s).",
            type=str,
            required=False
        )
        parser.add_argument(
            '-m',
            help="Root folder for media files.",
            type=str,
            required=False
        )
        parser.add_argument(
            '-p',
            help="Path to alternative preferences json file. Defaults to ./prefs.json .",
            type=str,
            required=False
        )
        return parser.parse_args()

    def get_prefs(pth, script_path):
        prefs = {}
        try:
            with open(pth, 'r') as json_data:
                prefs = json.load(json_data)

            fp = prefs['media_meta']['ffprobe_path']
            if fp.startswith('./'):
                prefs['media_meta']['ffprobe_path'] = script_path + fp[1:]
            if platform.system() == 'Windows' and not fp.endswith('.exr'):
                prefs['media_meta']['ffprobe_path'] += '.exe'
            if not os.path.exists(prefs['media_meta']['ffprobe_path']):
                logger.error(f"Ffprobe not found at {prefs['media_meta']['ffprobe_path']}, exiting")
                exit(1)

            op = prefs['media_meta']['oiio_path']
            if op.startswith('./'):
                prefs['media_meta']['oiio_path'] = script_path + op[1:]
            if platform.system() == 'Windows' and not op.endswith('.exr'):
                prefs['media_meta']['oiio_path'] += '.exe'
            if not os.path.exists(prefs['media_meta']['oiio_path']):
                logger.error("OiioTool not found at {}.".format(prefs['media_meta']['oiio_path']))
        except Exception as e:
            # no prefs found
            logger.error("Error opening prefs file {}\n{}".format(str(pth), e))
        return prefs

    # command line args
    args = vars(get_args())

    # read prefs file
    cmd_prefs = args.get('p')
    if not cmd_prefs or cmd_prefs == '':
        cmd_prefs = script_path + '/prefs.json'
    prefs = get_prefs(cmd_prefs, script_path)
    if prefs is None or prefs == {}:
        logger.error("Preferences not found, exiting.")
        exit(1)

    # command line arguments for csv and media folders have precedence
    search_csv_root = args.get('i')
    if search_csv_root:
        prefs['search_csv']['root_folder'] = search_csv_root
    search_media_root = args.get('m')
    if search_media_root:
        prefs['search_media']['root_folder'] = search_media_root
    logger.info(f"Staring with media at \n{prefs['search_media']['root_folder']}\nwith csvs at\n{prefs['search_csv']['root_folder']}\n")

    tool = EditTool(prefs, script_path)
    tool.read_csvs()
    tool.find_media()
    tool.prep_matching()
    tool.csv_matching()
    try:
        pass
    except Exception as e:
        logger.error(e)
        exit(1)

    tool.matched_media_to_edls()


if __name__ == "__main__":
    main()

