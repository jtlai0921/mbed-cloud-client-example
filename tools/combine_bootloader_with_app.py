#!/usr/bin/env python

## ----------------------------------------------------------------------------
## Copyright 2016-2017 ARM Ltd.
##
## SPDX-License-Identifier: Apache-2.0
##
## Licensed under the Apache License, Version 2.0 (the "License");
## you may not use this file except in compliance with the License.
## You may obtain a copy of the License at
##
##     http://www.apache.org/licenses/LICENSE-2.0
##
## Unless required by applicable law or agreed to in writing, software
## distributed under the License is distributed on an "AS IS" BASIS,
## WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
## See the License for the specific language governing permissions and
## limitations under the License.
## ----------------------------------------------------------------------------

from os import path
import json
import hashlib, zlib, struct
import time
import sys
from intelhex import IntelHex

'''
define FIRMWARE_HEADER_MAGIC   0x5a51b3d4UL
define FIRMWARE_HEADER_VERSION 2
define ARM_UC_SHA512_SIZE     (512/8)
define ARM_UC_GUID_SIZE       (128/8)
typedef struct _arm_uc_internal_header_t
{
    /* Metadata-header specific magic code */
    uint32_t headerMagic;

    /* Revision number for metadata header. */
    uint32_t headerVersion;

    /* Version number accompanying the firmware. Larger numbers imply more
       recent and preferred versions. This is used for determining the
       selection order when multiple versions are available. For downloaded
       firmware the manifest timestamp is used as the firmware version.
    */
    uint64_t firmwareVersion;

    /* Total space (in bytes) occupied by the firmware BLOB. */
    uint64_t firmwareSize;

    /* Firmware hash calculated over the firmware size. Should match the hash
       generated by standard command line tools, e.g., shasum on Linux/Mac.
    */
    uint8_t firmwareHash[ARM_UC_SHA512_SIZE];

    /* The ID for the update campaign that resulted in the firmware update.
    */
    uint8_t campaign[ARM_UC_GUID_SIZE];

    /* Size of the firmware signature. Must be 0 if no signature is supplied. */
    uint32_t firmwareSignatureSize;

    /* Header 32 bit CRC. Calculated over the entire header, including the CRC
       field, but with the CRC set to zero.
    */
    uint32_t headerCRC;

    /* Optional firmware signature. Hashing algorithm should be the same as the
       one used for the firmware hash. The firmwareSignatureSize must be set.
    */
    uint8_t firmwareSignature[0];
} arm_uc_internal_header_t;
'''

# define defaults to go into the metadata header
SIZEOF_SHA512     = int(512/8)
SIZEOF_GUID       = int(128/8)
FIRMWARE_HEADER_MAGIC = 0x5a51b3d4
FIRMWARE_HEADER_VERSION = 2
header_format = ">2I2Q{}s{}s2I".format(SIZEOF_SHA512, SIZEOF_GUID)

if sys.version_info < (3,):
    def b(x):
        return bytearray(x)
else:
    def b(x):
        return x

def create_header(app_blob, firmwareVersion):
    # calculate the hash of the application
    firmwareHash = hashlib.sha256(app_blob).digest()

    # calculate the total size which is defined as the application size + metadata header
    firmwareSize = len(app_blob)

    # set campaign GUID to 0
    campaign = b'\00'

    # signature not supported, set size to 0
    signatureSize = 0

    print ('imageSize:    {}'.format(firmwareSize))
    print ('imageHash:    {}'.format(''.join(['{:0>2x}'.format(c) for c in b(firmwareHash)])))
    print ('imageversion: {}'.format(firmwareVersion))

    # construct struct for CRC calculation
    headerCRC = 0
    FirmwareHeader = struct.pack(header_format,
                                 FIRMWARE_HEADER_MAGIC,
                                 FIRMWARE_HEADER_VERSION,
                                 firmwareVersion,
                                 firmwareSize,
                                 firmwareHash,
                                 campaign,
                                 signatureSize,
                                 headerCRC)

    # calculate checksum over header, including signatureSize but without headerCRC
    headerCRC = zlib.crc32(FirmwareHeader[:-4]) & 0xffffffff

    # Pack the data into a binary blob
    FirmwareHeader = struct.pack(header_format,
                                 FIRMWARE_HEADER_MAGIC,
                                 FIRMWARE_HEADER_VERSION,
                                 firmwareVersion,
                                 firmwareSize,
                                 firmwareHash,
                                 campaign,
                                 signatureSize,
                                 headerCRC)

    return FirmwareHeader


def combine(bootloader_fn, app_fn, app_addr, hdr_addr, bootloader_addr, output_fn, version, no_bootloader):
    ih = IntelHex()

    bootloader_format = bootloader_fn.split('.')[-1]

    # write the bootloader
    if not no_bootloader:
        print("Using bootloader %s" % bootloader_fn)
        if bootloader_format == 'hex':
            print("Loading bootloader from hex file.")
            ih.fromfile(bootloader_fn, format=bootloader_format)
        elif bootloader_format == 'bin':
            print("Loading bootloader to address 0x%08x." % bootloader_addr)
            ih.loadbin(bootloader_fn, offset=bootloader_addr)
        else:
            print('Bootloader format can only be .bin or .hex')
            exit(-1)

    # write firmware header
    app_format=app_fn.split('.')[-1]
    if app_format == 'bin':
        with open(app_fn, 'rb') as fd:
            app_blob = fd.read()
    elif app_format == 'hex':
        application = IntelHex(app_fn)
        app_blob = application.tobinstr()
    FirmwareHeader = create_header(app_blob, version)
    print("Writing header to address 0x%08x." % hdr_addr)
    ih.puts(hdr_addr, FirmwareHeader)

    # write the application
    if app_format == 'bin':
        print("Loading application to address 0x%08x." % app_addr)
        ih.loadbin(app_fn, offset=app_addr)
    elif app_format == 'hex':
        print("Loading application from hex file")
        ih.fromfile(app_fn, format=app_format)

    # output to file
    ih.tofile(output_fn, format=output_fn.split('.')[-1])


if __name__ == '__main__':
    from glob import glob
    import argparse

    parser = argparse.ArgumentParser(
        description='Combine bootloader with application adding metadata header.')

    def addr_arg(s):
        if not isinstance(s, int):
            s = eval(s)

        return s

    bin_map = {
        'k64f': {
            'mem_start': '0x0'
        },
        'ublox_evk_odin_w2': {
            'mem_start': '0x08000000'
        },
        'nucleo_f429zi': {
            'mem_start': '0x08000000'
        },
        'numaker_pfm_nuc472': {
            'mem_start': '0x0'
        },
        'numaker_pfm_m487': {
            'mem_start': '0x0'
        }
    }

    curdir = path.dirname(path.abspath(__file__))

    def parse_mbed_app_addr(mcu, key):
        mem_start = bin_map[mcu]["mem_start"]
        with open(path.join(curdir, "..", "mbed_app.json")) as fd:
            mbed_json = json.load(fd)
            addr = mbed_json["target_overrides"][mcu.upper()][key]
            return addr_arg(addr)

    # specify arguments
    parser.add_argument('-m', '--mcu', type=lambda s : s.lower().replace("-","_"), required=False,
                        help='mcu', choices=bin_map.keys())
    parser.add_argument('-b', '--bootloader',    type=argparse.FileType('rb'),     required=False,
                        help='path to the bootloader binary')
    parser.add_argument('-a', '--app',           type=argparse.FileType('rb'),     required=True,
                        help='path to application binary')
    parser.add_argument('-c', '--app-addr',      type=addr_arg,                    required=False,
                        help='address of the application')
    parser.add_argument('-d', '--header-addr',   type=addr_arg,                    required=False,
                        help='address of the firmware metadata header')
    parser.add_argument('-o', '--output',        type=argparse.FileType('wb'),     required=True,
                        help='output combined file path')
    parser.add_argument('-s', '--set-version',   type=int,                         required=False,
                        help='set version number', default=int(time.time()))
    parser.add_argument('-nb', '--no-bootloader',action='store_true',              required=False,
                        help='Produce output without bootloader. The output only '+
                             'contains header + app. requires hex output format')

    # workaround for http://bugs.python.org/issue9694
    parser._optionals.title = "arguments"

    # get and validate arguments
    args = parser.parse_args()

    # validate the output format
    f = args.output.name.split('.')[-1]
    if f == 'hex':
        output_format = 'hex'
    elif f == 'bin':
        output_format = 'bin'
    else:
        print('Output format can only be .bin or .hex')
        exit(-1)

    # validate no-bootloader option
    if args.no_bootloader and output_format == 'bin':
        print('--no-bootloader option requires the output format to be .hex')
        exit(-1)

    # validate that we can find a bootloader or no_bootloader is specified
    bootloader = None
    if not args.no_bootloader:
        if args.mcu and not args.bootloader:
            bl_list = glob("tools/mbed-bootloader-{}-*".format(args.mcu))
            if len(bl_list) == 0:
                print("Specified MCU does not have a binary in this location " + \
                      "Please specify bootloader location with -b")
                exit(-1)
            elif len(bl_list) > 1:
                print("Specified MCU have more than one binary in this location " + \
                      "Please specify bootloader location with -b")
                print(bl_list)
                exit(-1)
            else:
                fname = bl_list[0]
                bootloader = open(fname, 'rb')
        elif args.bootloader:
            bootloader = args.bootloader
        elif not (args.mcu or args.bootloader):
            print("Please specify bootloader location -b or MCU -m")
            exit(-1)

    # get the path of bootloader, application and output
    if bootloader:
        bootloader_fn = path.abspath(bootloader.name)
        bootloader.close()
    else:
        bootloader_fn = ''

    if bootloader_fn.split('.')[-1] != 'hex' and not args.mcu:
        print("Please provide a bootloader in hex format or specify MCU -m")
        exit(-1)

    app_fn = path.abspath(args.app.name)
    args.app.close()
    output_fn = path.abspath(args.output.name)
    args.output.close()

    # Use specified addresses or default if none are provided
    app_format = app_fn.split('.')[-1]
    if(not (args.mcu or args.app_addr or app_format == 'hex')):
        print("Please specify app address or MCU")
        exit(-1)
    if app_format != 'hex':
        app_addr = args.app_addr or parse_mbed_app_addr(args.mcu, "target.mbed_app_start")
    else:
        app_addr = None

    if args.mcu:
        mem_start = addr_arg(bin_map[args.mcu]["mem_start"])
    else:
        mem_start = 0

    if(not (args.mcu or args.header_addr)):
        print("Please specify header address or MCU")
        exit(-1)
    header_addr = args.header_addr or parse_mbed_app_addr(args.mcu, "update-client.application-details")

    # combine application and bootloader adding metadata info
    combine(bootloader_fn, app_fn, app_addr, header_addr, mem_start,
            output_fn, args.set_version, args.no_bootloader)

    # print the output file path
    print('Combined binary:' + output_fn)
