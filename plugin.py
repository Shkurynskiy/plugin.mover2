#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    Written by:               Josh.5 <jsunnex@gmail.com>
    Date:                     22 Aug 2021, (9:55 PM)

    Copyright:
        Copyright (C) 2021 Josh Sunnex

        This program is free software: you can redistribute it and/or modify it under the terms of the GNU General
        Public License as published by the Free Software Foundation, version 3.

        This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
        implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
        for more details.

        You should have received a copy of the GNU General Public License along with this program.
        If not, see <https://www.gnu.org/licenses/>.

"""
import hashlib
import json
import logging
import os
from configparser import NoSectionError, NoOptionError

from unmanic.libs.directoryinfo import UnmanicDirectoryInfo
from unmanic.libs.unplugins.settings import PluginSettings

# Configure plugin logger
logger = logging.getLogger("Unmanic.Plugin.mover2")


class Settings(PluginSettings):
    settings = {
        "force_processing_all_files":   False,
        "destination_directory":        "/library",
        "recreate_directory_structure": True,
        "include_library_structure":    True,
        "remove_source_file":           False,
        "preserve_file_mtime":          True,
    }

    def __init__(self, *args, **kwargs):
        super(Settings, self).__init__(*args, **kwargs)
        self.form_settings = {
            "force_processing_all_files":   {
                "label": "Force processing of all files",
            },
            "destination_directory":        {
                "label":      "Destination directory",
                "input_type": "browse_directory",
            },
            "recreate_directory_structure": {
                "label": "Recreate directory structure",
            },
            "include_library_structure":    self.__set_include_library_structure(),
            "remove_source_file":           {
                "label": "Remove source files",
            },
            "preserve_file_mtime":          {
                "label": "Preserve original file modification time (mtime)",
            },
        }

    def __set_include_library_structure(self):
        values = {
            "label":      "Also include library path when re-creating the directory structure",
            "input_type": "checkbox",
        }
        if not self.get_setting('recreate_directory_structure'):
            values["display"] = 'hidden'
        return values


def all_parent_directories(head):
    dirs = []
    while True:
        head, tail = os.path.split(head)
        if tail != "":
            dirs[0:0] = [tail]
        else:
            # Dont bother appending the root to this path...
            break
    return dirs


def get_file_out(settings, original_source_path, file_out, library_id=None):
    # Get the destination directory
    destination_directory = settings.get_setting('destination_directory')

    # Is the plugin configured to recreate the directory structure?
    if settings.get_setting('recreate_directory_structure'):
        # Recreate the FULL directory path
        # Get the parent directory of the original file
        # Eg. /library/path/to/my/file.ext    =   /library/path/to/my
        original_source_dirname = os.path.dirname(original_source_path)

        if not settings.get_setting('include_library_structure'):
            # Remove the library path from the original_source_dirname
            if library_id:
                from unmanic.libs.library import Library
                library = Library(library_id)
                library_path = library.get_path()
            else:
                from unmanic import config
                unmanic_settings = config.Config()
                library_path = unmanic_settings.get_library_path()
            original_source_dirname = os.path.relpath(original_source_dirname, library_path)

        # Fetch a list of all directories in the original directory path
        # Eg. directories = ['library', 'path', 'to', 'my']
        directories = all_parent_directories(original_source_dirname)

        # Append the directory structure to the destination directory
        destination_directory = os.path.join(destination_directory, *directories)

        # Ensure the destination directory structure exists. Create it if it does not
        if not os.path.exists(destination_directory):
            os.makedirs(destination_directory)

    # Get just the file name from of the output file
    file_out_basename = os.path.basename(file_out)

    # Return the output file
    return os.path.join(destination_directory, file_out_basename)


def file_marked_as_moved(path, file_metadata=None):
    if file_metadata:
        try:
            metadata = file_metadata.get()
            if metadata.get('status') == 'Ignoring' or metadata.get('ignore') is True:
                return True
        except Exception as e:
            logger.debug("Unable to read UnmanicFileMetadata for '{}': {}".format(path, e))

    directory_info = UnmanicDirectoryInfo(os.path.dirname(path))
    try:
        has_been_moved = directory_info.get('mover2', os.path.basename(path))
    except NoSectionError as e:
        has_been_moved = ''
    except NoOptionError as e:
        has_been_moved = ''
    except Exception as e:
        logger.debug("Unknown exception {}.".format(e))
        has_been_moved = ''

    if has_been_moved == 'Ignoring':
        # This file movement already has been attempted and failed
        return True

    # Default to...
    return False


def on_library_management_file_test(data, task_data_store=None, file_metadata=None):
    """
    Runner function - enables additional actions during the library management file tests.

    The 'data' object argument includes:
        path                            - String containing the full path to the file being tested.
        issues                          - List of currently found issues for not processing the file.
        add_file_to_pending_tasks       - Boolean, is the file currently marked to be added to the queue for processing.

    :param data:
    :return:

    """
    # Get the path to the file
    abspath = data.get('path')
    basename = os.path.basename(abspath)

    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    if file_marked_as_moved(abspath, file_metadata=file_metadata):
        # Ensure this file is not added to the pending tasks
        data['add_file_to_pending_tasks'] = False
        logger.debug("File '{}' has been previously marked as moved.".format(abspath))
    elif settings.get_setting('force_processing_all_files') and basename != '.unmanic':
        # (Never move the .unmanic file)
        # Ensure this file is added to the pending tasks regardless of status of any subsequent tests
        data['add_file_to_pending_tasks'] = True
        logger.debug("Forcing file '{}' to be added to task list.".format(abspath))

    return data


def on_postprocessor_file_movement(data, task_data_store=None, file_metadata=None):
    """
    Runner function - configures additional postprocessor file movements during the postprocessor stage of a task.
    """
    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    # Get the original file's absolute path
    original_source_path = data.get('source_data', {}).get('abspath')
    if not original_source_path:
        logger.error("Provided 'source_data' is missing the source file abspath data.")
        return data

    unmanic_destination_file = data.get('file_out')

    # Read timestamps from the original source file
    source_atime = None
    source_mtime = None
    if original_source_path and os.path.exists(original_source_path):
        try:
            stat_info = os.stat(original_source_path)
            source_atime = stat_info.st_atime
            source_mtime = stat_info.st_mtime
        except Exception as e:
            logger.error("Unable to read source file stat for '{}': {}".format(original_source_path, e))

    # Should the plugin remove the source file?
    data['remove_source_file'] = settings.get_setting('remove_source_file')
    # Always prevent Unmanic from running the default file movement (requires Unmanic v0.2.0)
    data['run_default_file_copy'] = False

    # Set the output file
    file_out = get_file_out(settings, original_source_path, os.path.abspath(data.get('file_out')),
                            library_id=data.get('library_id'))
    data['file_out'] = file_out

    # Set plugin to copy file
    data['copy_file'] = True

    # Store required data in JSON file for on_postprocessor_task_results
    profile_directory = settings.get_profile_directory()
    src_file_hash = hashlib.md5(original_source_path.encode('utf8')).hexdigest()
    plugin_data_file = os.path.join(profile_directory, '{}.json'.format(src_file_hash))
    with open(plugin_data_file, 'w') as f:
        required_data = {
            'file_out':                 file_out,
            'unmanic_destination_file': unmanic_destination_file,
            'source_atime':             source_atime,
            'source_mtime':             source_mtime,
        }
        json.dump(required_data, f, indent=4)

    if not settings.get_setting('remove_source_file'):
        if not os.path.exists(original_source_path):
            logger.error("Original source path could not be found.")
            return data

        # Mark the source file to be ignored on subsequent scans
        if file_metadata:
            file_metadata.set({'status': 'Ignoring'}, use_source_scope=True)
            logger.debug("Ignore on next scan written for '{}'.".format(original_source_path))
        else:
            directory_info = UnmanicDirectoryInfo(os.path.dirname(original_source_path))
            directory_info.set('mover2', os.path.basename(original_source_path), 'Ignoring')
            directory_info.save()
            logger.debug("Ignore on next scan written for '{}'.".format(original_source_path))


def on_postprocessor_task_results(data):
    """
    Runner function - provides a means for additional postprocessor functions based on the task success.
    """
    # Configure settings object (maintain compatibility with v1 plugins)
    if data.get('library_id'):
        settings = Settings(library_id=data.get('library_id'))
    else:
        settings = Settings()

    # Get the original file's absolute path
    original_source_path = data.get('source_data', {}).get('abspath')
    if not original_source_path:
        logger.error("Provided 'source_data' is missing the source file abspath data.")
        return data

    # Read the data from the on_postprocessor_file_movement runner
    profile_directory = settings.get_profile_directory()
    src_file_hash = hashlib.md5(original_source_path.encode('utf8')).hexdigest()
    plugin_data_file = os.path.join(profile_directory, '{}.json'.format(src_file_hash))
    if not os.path.exists(plugin_data_file):
        logger.error("Plugin data file is missing (This may be because the file movement post-processor was skipped)")
        raise Exception("Plugin data file is missing.")
    with open(plugin_data_file) as infile:
        file_movement_data = json.load(infile)

    # Ensure the file was correctly moved as required
    if not data.get('file_move_processes_success'):
        if not file_movement_data.get('file_out') in data.get('destination_files'):
            logger.error(
                "Detected that the file movement did not complete correctly. Dont do anything with the source file.")
            return data

    # Apply preserved mtime to destination file if enabled
    if settings.get_setting('preserve_file_mtime'):
        file_out_path = file_movement_data.get('file_out')
        source_mtime = file_movement_data.get('source_mtime')
        source_atime = file_movement_data.get('source_atime')

        # Fallback: read directly from original source if file wasn't recorded
        if not source_mtime and os.path.exists(original_source_path):
            try:
                stat_info = os.stat(original_source_path)
                source_atime = stat_info.st_atime
                source_mtime = stat_info.st_mtime
            except Exception as e:
                logger.error("Unable to read original source stat: {}".format(e))

        if file_out_path and os.path.exists(file_out_path) and source_mtime:
            try:
                atime_to_set = source_atime if source_atime is not None else source_mtime
                os.utime(file_out_path, (atime_to_set, source_mtime))
                logger.info("Successfully preserved original mtime ({}) on destination file '{}'".format(source_mtime, file_out_path))
            except Exception as e:
                logger.error("Failed to set mtime on destination file '{}': {}".format(file_out_path, e))

    # Remove source file if configured
    if settings.get_setting('remove_source_file'):
        unmanic_destination_file = os.path.abspath(file_movement_data.get('unmanic_destination_file'))
        if os.path.exists(unmanic_destination_file):
            logger.debug("Plugin is configured to ensure the original file is removed. Removing file '{}'".format(
                unmanic_destination_file))
            os.remove(unmanic_destination_file)
        else:
            logger.debug("Plugin is configured to ensure the original file is removed. File has already been removed.")

    # Clean up plugin's data file
    os.remove(plugin_data_file)
