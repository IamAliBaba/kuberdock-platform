#!/usr/bin/env python2
"""The module provides commands to manage zfs volumes for localstorage backend.

It creates one zpool on top of specified block devices. Poll may be extended
with additional devices later.
This pool will be mounted to /var/lib/kuberdock/storage mountpoint.
Each created volume (do_add_volume) will be ZFS FS in this zpool with
specified size quota.

Requires working zfs commands on the host.

"""
from __future__ import absolute_import

import os
import re

from .common import (
    OK, ERROR, LOCAL_STORAGE_MOUNT_POINT, get_fs_usage, silent_call,
    get_subprocess_result, raise_cmd_error, get_path_relative_to_localstorage)

# Storage backend name, will be used in top wrapper (manage.py) messages.
VOLUME_MANAGE_NAME = 'ZFS'

# Volume group name for kuberdock storage
KD_ZPOOL_NAME = 'kdstorage00'


def do_add_volume(call_args):
    devices = call_args.devices
    return add_devices_to_localstorage(devices)


def _list_zpools():
    err_code, output = get_subprocess_result(
        ['zpool', 'list', '-H', '-o', 'name']
    )
    raise_cmd_error(err_code, output)
    return [name for name in output.split('\n') if name]


def init_kd_zpool(devices):
    """Creates and mounts Kuberdock zpool."""
    err_code, output = get_subprocess_result(
        ['zpool', 'create', '-f', KD_ZPOOL_NAME] + devices
    )
    raise_cmd_error(err_code, output)
    err_code, output = get_subprocess_result(
        ['zfs', 'set', 'mountpoint={}'.format(LOCAL_STORAGE_MOUNT_POINT),
         KD_ZPOOL_NAME]
    )
    raise_cmd_error(err_code, output)


def extend_kd_zpool(devices):
    err_code, output = get_subprocess_result(
        ['zpool', 'add', '-f', KD_ZPOOL_NAME] + devices
    )
    raise_cmd_error(err_code, output)


def _get_zpool_properties(zpool_name):
    """Returns list of devices used in KD zpool with size.
    :return: dict {'device name': {'size': <size of device>}, ...}
    """
    devices = get_device_list(zpool_name)
    result = {}
    for dev in devices:
        err, output = get_subprocess_result(['blockdev', '--getsize64', dev])
        size = 0
        try:
            if not err:
                size = int(output.replace('\n', ''))
        except:
            pass
        result[dev] = {'size': size}
    return result


def get_device_list(zpool_name):
    """Returns list of devices used in KD storage zpool.
    """
    # This command will return simething like
    #   pool: kdstorage00
    # state: ONLINE
    # scan: none requested
    # config:
    #
    #   NAME        STATE     READ WRITE CKSUM
    #       kdstorage00  ONLINE       0     0     0
    #       sdc       ONLINE       0     0     0
    #       sdd       ONLINE       0     0     0
    #
    # We have to parse it output to extract devices
    err_code, output = get_subprocess_result(
        ['zpool', 'status', KD_ZPOOL_NAME]
    )
    raise_cmd_error(err_code, output)
    header_pattern = re.compile(r'^\s+' + zpool_name + r'\s+')
    device_string_pattern = re.compile(r'^\s+([\w\d\-_]+)\s+')
    # Parser states:
    #   initial parsing, no header had met
    state_init = 1
    #   header already passed, now expect strings with device names
    state_parse = 2
    state = state_init
    devices = []
    for line in output.split('\n'):
        if state == state_init:
            if header_pattern.match(line):
                state = state_parse
            continue
        if state == state_parse:
            m = device_string_pattern.match(line)
            if not m:
                continue
            dev = '/dev/' + m.group(1)
            if not os.path.exists(dev):
                continue
            devices.append(dev)
    return devices


def do_get_info(_):
    all_names = _list_zpools()
    if KD_ZPOOL_NAME not in all_names:
        return ERROR, {'message': 'KD zpool not found on the host'}

    dev_info = _get_zpool_properties(KD_ZPOOL_NAME)
    return OK, {
        'lsUsage': get_fs_usage(LOCAL_STORAGE_MOUNT_POINT),
        'zpoolDevs': dev_info
    }


def do_remove_storage(_):
    """Destroys zpool created for local storage.
    Returns tuple of success flag and list of devices which were used in
    destroyed zpool.
    Result of the function will be additionally processed, so it does not
    return readable statuses of performed operation.

    """
    all_names = _list_zpools()
    if KD_ZPOOL_NAME not in all_names:
        return True, []
    try:
        devices = get_device_list(KD_ZPOOL_NAME)
    except:
        return (
            False,
            'Failed to get device list in zpool "{}"'.format(KD_ZPOOL_NAME)
        )
    try:
        silent_call(['zpool', 'destroy', '-f', KD_ZPOOL_NAME])
        return True, devices
    except:
        return False, 'Failed to delete zpool {}'.format(KD_ZPOOL_NAME)


def add_devices_to_localstorage(devices):
    """Initializes KD zpool: Creates it if it not exists. Adds devices to
    zpool.
    """
    all_names = _list_zpools()
    if KD_ZPOOL_NAME not in all_names:
        init_kd_zpool(devices)
    else:
        err_code, output = get_subprocess_result(
            ['zpool', 'add', '-f', KD_ZPOOL_NAME] + devices
        )
        raise_cmd_error(err_code, output)
    dev_info = _get_zpool_properties(KD_ZPOOL_NAME)
    return OK, {
        'lsUsage': get_fs_usage(LOCAL_STORAGE_MOUNT_POINT),
        'zpoolDevs': dev_info
    }


def full_volume_path_to_zfs_path(path):
    """Converts volume path in form '/var/lib/kuberdock/storage/123/pvname'
    to path that may used in zfs commands:
        'kdstorage00/123/pvname'
    It is assumed that incoming path is a subdirectory of
    /var/lib/kuberdock/storage - mountpoint of kuberdock zpool.

    """
    relative_path = get_path_relative_to_localstorage(path)
    zfs_path = '{}/{}'.format(KD_ZPOOL_NAME, relative_path)
    return zfs_path


def do_create_volume(call_args):
    """Creates zfs filesystem on specified path and sets size quota to it.
    :param call_args.path: relative (from KD storage dir) path to volume
    :param call_args.quota: FS quota for the volume
    :return: full path to created volume
    """
    path = call_args.path
    quota_gb = call_args.quota
    zfs_path = full_volume_path_to_zfs_path(path)
    err_code, output = get_subprocess_result(['zfs', 'create', '-p', zfs_path])
    raise_cmd_error(err_code, output)
    err_code, output = get_subprocess_result(
        ['zfs', 'set', 'quota={}G'.format(quota_gb), zfs_path]
    )
    raise_cmd_error(err_code, output)
    return OK, {'path': path}


def do_remove_volume(call_args):
    path = call_args.path
    zfs_path = full_volume_path_to_zfs_path(path)

    err_code, output = get_subprocess_result([
        'zfs', 'destroy', '-r', '-f', zfs_path
    ])
    raise_cmd_error(err_code, output)


def do_resize_volume(call_args):
    path = call_args.path
    quota_gb = call_args.new_quota
    zfs_path = full_volume_path_to_zfs_path(path)
    err_code, output = get_subprocess_result(
        ['zfs', 'set', 'quota={}G'.format(quota_gb), zfs_path]
    )
    raise_cmd_error(err_code, output)
    return OK, {'path': path}