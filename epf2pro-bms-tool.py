#!/usr/bin/env python3
# -*- coding: utf-8 -*-
################################################################################
# BMS Monitoring Script for ePowerFun ePF-2 PRO 653Wh Battery.
# 
# MIT License
# 
# Copyright (c) Cymaphore
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# 
# DISCLAIMER:
# 
# This script is the result of reverse engineering the battery protocol
# of the "ePowerFun ePF-2 PRO 653Wh" battery (type FM-1304-3400-RS1).
# It is neither complete or intended for any productive use and was purely
# created out of personal interest.
#
# Batteries pose inherent risks (fire, explosion, etc.)! Use this script AT YOUR
# OWN RISK. Many aspects are still speculative in nature or may be plainly wrong!
#
################################################################################

import serial
import argparse
import time
import datetime
import csv
import json
import os

###### CONFIGURATION

# TRANSPORT LAYER SPEC
TRANSPORT_LAYER_SPEC = {
    'FRAME_LENGTH': 85,
    'FRAME_START': b'\xeb\x90',
    'PAYLOAD_START_BYTE': 6,
    'COMMAND_ID_BYTES': slice(4, 6),
    'SERIAL_CONFIG': {
        'port': '/dev/ttyUSB0', 'baudrate': 9600, 'bytesize': serial.EIGHTBITS,
        'parity': serial.PARITY_NONE, 'stopbits': serial.STOPBITS_ONE, 'timeout': 1.0
    },
    'CHECKSUM': {
        'byte_index': 84,
        # TODO fix it, checkum doesn't work yet, missed something
        'method': 'XOR_SUM', 
        'range_end_exclusive': 84 
    }
}

# PAYLOAD SIGNAL SPECIFICATION (relative to PAYLOAD_START_BYTE (i.e., Byte 6))
# Used bit positioning because i'm used to it from CAN-Bus.
PAYLOAD_SIGNAL_SPEC = {
    'PACK_VOLTAGE': {
        'type': 'pack_voltage', 'title': 'Pack Voltage',
        'description': 'Total battery pack voltage.',
        'start_bit': 0, 'length_bits': 16, 'big_endian': True,
        'signed': False, 'gain': 0.01, 'offset': 0, 'unit': 'V',
    },
    'PACK_CURRENT': {
        'type': 'pack_current', 'title': 'Pack Current',
        'description': 'Battery current',
        'start_bit': 16, 'length_bits': 16, 'big_endian': True,
        'signed': True, 'gain': 0.1, 'offset': 0, 'unit': 'A',
    },
    'SOC': {
        'type': 'soc', 'title': 'State of Charge',
        'description': 'State of charge.',
        'start_bit': 128, 'length_bits': 8, 'big_endian': True,
        'signed': False, 'gain': 1.0, 'offset': 0, 'unit': '%',
    },
    'STATUS_FLAGS_0F_10': {
        'type': 'status_raw', 'title': 'Maybe Protection Flags (TODO)',
        'description': 'Raw status flags covering OV/UV/OT/OC protections. Not yet sure and didnt try.',
        'start_bit': 72, 'length_bits': 16, 'big_endian': True,
        'signed': False, 'gain': 1.0, 'offset': 0, 'unit': 'Raw',
    },

    'FET_DISCHARGE_ACTIVE': {
        'type': 'status_bit', 'title': 'Discharge FET Status',
        'description': 'Guess: Indicates if the Discharge FET is closed',
        'start_bit': 98, 'length_bits': 1, 'big_endian': True,
        'signed': False, 'gain': 1.0, 'offset': 0, 'unit': 'Status',
        'value_map': {0: 'Inactive', 1: 'Active'}
    },
    'FET_CHARGE_ACTIVE': {
        'type': 'status_bit', 'title': 'Charge FET Status',
        'description': 'Guess: Indicates if the Charge FET is closed',
        'start_bit': 100, 'length_bits': 1, 'big_endian': True,
        'signed': False, 'gain': 1.0, 'offset': 0, 'unit': 'Status',
        'value_map': {0: 'Inactive', 1: 'Active'}
    },
    'CHARGE_PIN_A_ACTIVE': {
        'type': 'status_bit', 'title': 'Charge Pin A Status',
        'description': 'Guess: Indicator for secondary charging path activity',
        'start_bit': 102, 'length_bits': 1, 'big_endian': True,
        'signed': False, 'gain': 1.0, 'offset': 0, 'unit': 'Status',
        'value_map': {0: 'Inactive', 1: 'Active'}
    },
    
    'TEMP_1': {
        'type': 'temperature', 'title': 'Temp Sensor 1 (Probably FET/Output)',
        'description': 'Sensor likely near FETs or power leads.',
        'start_bit': 592, 'length_bits': 8, 'big_endian': True,
        'signed': True, 'gain': 1.0, 'offset': 0, 'unit': '°C'
    },
    'TEMP_2': {
        'type': 'temperature', 'title': 'Temp Sensor 2 (Internal Cell)',
        'description': 'Internal sensor, usually located near the cell groups.',
        'start_bit': 600, 'length_bits': 8, 'big_endian': True,
        'signed': True, 'gain': 1.0, 'offset': 0, 'unit': '°C'
    },
    'TEMP_3': {
        'type': 'temperature', 'title': 'Temp Sensor 3 (Internal Cell)',
        'description': 'Internal sensor, usually located near the cell groups.',
        'start_bit': 608, 'length_bits': 8, 'big_endian': True,
        'signed': True, 'gain': 1.0, 'offset': 0, 'unit': '°C'
    },
    'TEMP_4': {
        'type': 'temperature', 'title': 'Temp Sensor 4 (Probably Cell or Ambient/Pack)',
        'description': 'Potentially ambient or far internal pack sensor.',
        'start_bit': 616, 'length_bits': 8, 'big_endian': True,
        'signed': True, 'gain': 1.0, 'offset': 0, 'unit': '°C'
    },

    'CELL_01_V': {'type': 'cell_voltage', 'title': 'Cell Group 01 (4P)', 'description': 'Voltage of series cell group 1.', 'start_bit': 376, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_02_V': {'type': 'cell_voltage', 'title': 'Cell Group 02 (4P)', 'description': 'Voltage of series cell group 2.', 'start_bit': 392, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_03_V': {'type': 'cell_voltage', 'title': 'Cell Group 03 (4P)', 'description': 'Voltage of series cell group 3.', 'start_bit': 408, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_04_V': {'type': 'cell_voltage', 'title': 'Cell Group 04 (4P)', 'description': 'Voltage of series cell group 4.', 'start_bit': 424, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_05_V': {'type': 'cell_voltage', 'title': 'Cell Group 05 (4P)', 'description': 'Voltage of series cell group 5.', 'start_bit': 440, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_06_V': {'type': 'cell_voltage', 'title': 'Cell Group 06 (4P)', 'description': 'Voltage of series cell group 6.', 'start_bit': 456, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_07_V': {'type': 'cell_voltage', 'title': 'Cell Group 07 (4P)', 'description': 'Voltage of series cell group 7.', 'start_bit': 472, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_08_V': {'type': 'cell_voltage', 'title': 'Cell Group 08 (4P)', 'description': 'Voltage of series cell group 8.', 'start_bit': 488, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_09_V': {'type': 'cell_voltage', 'title': 'Cell Group 09 (4P)', 'description': 'Voltage of series cell group 9.', 'start_bit': 504, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_10_V': {'type': 'cell_voltage', 'title': 'Cell Group 10 (4P)', 'description': 'Voltage of series cell group 10.', 'start_bit': 520, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_11_V': {'type': 'cell_voltage', 'title': 'Cell Group 11 (4P)', 'description': 'Voltage of series cell group 11.', 'start_bit': 536, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_12_V': {'type': 'cell_voltage', 'title': 'Cell Group 12 (4P)', 'description': 'Voltage of series cell group 12.', 'start_bit': 552, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
    'CELL_13_V': {'type': 'cell_voltage', 'title': 'Cell Group 13 (4P)', 'description': 'Voltage of series cell group 13.', 'start_bit': 568, 'length_bits': 16, 'big_endian': True, 'signed': False, 'gain': 0.001, 'offset': 0, 'unit': 'V'},
}


def extract_signal_bit_level(payload: bytes, config: dict) -> int:
    start_bit = config['start_bit']
    length_bits = config['length_bits']
    signed = config.get('signed', False)
    big_endian = config.get('big_endian', True) 
    
    start_byte_index = start_bit // 8
    end_bit_index = start_bit + length_bits
    end_byte_index = (end_bit_index + 7) // 8 
    
    required_bytes = payload[start_byte_index:end_byte_index]
    
    if not required_bytes:
        raise ValueError("Payload segment too short or configuration error.")

    byte_order_to_use = 'big' if big_endian else 'little'
    raw_int = int.from_bytes(required_bytes, byteorder=byte_order_to_use, signed=False)

    lsb_position_in_chunk = (end_byte_index * 8) - end_bit_index
    
    shifted_int = raw_int >> lsb_position_in_chunk

    mask = (1 << length_bits) - 1
    final_value = shifted_int & mask

    if signed and (final_value & (1 << (length_bits - 1))):
        sign_mask = (1 << length_bits)
        final_value = final_value - sign_mask

    return final_value

def calculate_checksum(data: bytes) -> int:
    if len(data) < 84: 
        return 0
    checksum = 0
    for byte in data[:84]:
        checksum ^= byte
    return checksum

def decode_frame(frame: bytes, detailed_cells: bool = False) -> dict:
    FRAME_LEN = TRANSPORT_LAYER_SPEC['FRAME_LENGTH']
    if len(frame) != FRAME_LEN:
        return {'ERROR': f'Invalid frame length: {len(frame)}'}
    if not frame.startswith(TRANSPORT_LAYER_SPEC['FRAME_START']):
        return {'ERROR': f'Invalid header: {frame[:2].hex()}'}

    # TODO: Faulty
    calculated_checksum = calculate_checksum(frame)
    reported_checksum_byte = TRANSPORT_LAYER_SPEC['CHECKSUM']['byte_index']
    reported_checksum = frame[reported_checksum_byte]
    
    is_valid = (calculated_checksum == reported_checksum)
    
    validity_str = 'PASS' if is_valid else f'FAIL (C:{calculated_checksum:02X}, R:{reported_checksum:02X})'
    
    payload_start = TRANSPORT_LAYER_SPEC['PAYLOAD_START_BYTE']
    payload_end = reported_checksum_byte 
    payload = frame[payload_start:payload_end]
    
    command_id = frame[TRANSPORT_LAYER_SPEC['COMMAND_ID_BYTES']].hex().upper()

    decoded_data = {
        'TIMESTAMP': datetime.datetime.now().isoformat(),
        'CHECKSUM_VALIDITY': validity_str,
        'CHECKSUM_VALID': int(is_valid),
        'COMMAND_ID': command_id,
        'FRAME_CHECKSUM_REPORTED': reported_checksum,
    }

    cell_voltages_list = [] 
    
    for name, config in PAYLOAD_SIGNAL_SPEC.items():
        try:
            raw_value = extract_signal_bit_level(payload, config)
            gain = config.get('gain', 1.0)
            offset = config.get('offset', 0)
            
            scaled_value = (raw_value * gain) + offset
            
            if 'value_map' in config:
                final_output = config['value_map'].get(raw_value, f'Unknown ({raw_value})')
            elif config.get('unit') == 'Raw':
                final_output = raw_value
            elif config.get('unit') == '°C': 
                final_output = round(scaled_value, 1)
            else:
                final_output = round(scaled_value, 3)
                
            decoded_data[name] = final_output
            
            if config['type'] == 'cell_voltage':
                cell_voltages_list.append(round(scaled_value, 3))
                
        except Exception:
            decoded_data[name] = None
    
    if any(v is not None and v > 0 for v in cell_voltages_list):
        valid_voltages = [v for v in cell_voltages_list if v is not None and v > 0]
        if valid_voltages:
            decoded_data['CELL_V_MIN'] = min(valid_voltages)
            decoded_data['CELL_V_MAX'] = max(valid_voltages)
    else:
        decoded_data['CELL_V_MIN'] = None
        decoded_data['CELL_V_MAX'] = None
        
    return decoded_data

CSV_FIELD_ORDER = [
    'TIMESTAMP', 
    'COMMAND_ID', 
    'CHECKSUM_VALID', 
    'FRAME_CHECKSUM_REPORTED', 
    'CELL_V_MIN', 
    'CELL_V_MAX'
]
CSV_FIELD_ORDER.extend(list(PAYLOAD_SIGNAL_SPEC.keys()))

class BMSSerialLogger:
    def __init__(self, base_prefix, log_types):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_types = log_types
        self.csv_filename = f'{base_prefix}_{timestamp}.csv'
        self.json_filename = f'{base_prefix}_{timestamp}.json'
        self.header_written = False

    def get_log_data(self, data: dict) -> dict:
        log_data = {}
        for key in CSV_FIELD_ORDER:
            if key in data and key != 'CHECKSUM_VALIDITY': 
                 log_data[key] = data[key]
        return log_data
    
    def log_csv(self, data: dict):
        if 'csv' not in self.log_types and 'both' not in self.log_types: return

        log_data = self.get_log_data(data)

        if not self.header_written:
            self.fieldnames = list(log_data.keys())
            try:
                with open(self.csv_filename, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=self.fieldnames, extrasaction='ignore')
                    writer.writeheader()
                    self.header_written = True
            except Exception:
                 self.header_written = False 

        if self.header_written:
            with open(self.csv_filename, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames, extrasaction='ignore')
                writer.writerow(log_data)

    def log_json(self, data: dict):
        if 'json' not in self.log_types and 'both' not in self.log_types: return
        
        log_data = self.get_log_data(data)
        
        with open(self.json_filename, 'a') as f:
            f.write(json.dumps(log_data) + '\n')

def get_signals_by_type(data: dict, signal_type: str):
    signals = []
    for name, config in PAYLOAD_SIGNAL_SPEC.items():
        if config['type'] == signal_type and name in data:
            signals.append({'key': name, 'title': config['title'], 'value': data[name], 'unit': config['unit']})
    return signals

def monitor_plain(data: dict, args: argparse.Namespace):
    print("-" * 40)
    print(f"[{data['TIMESTAMP'][:19]} | CS: {data['CHECKSUM_VALIDITY']} | CMD: {data['COMMAND_ID']}]")

    print(f"Voltage: {data.get('PACK_VOLTAGE', '-')} V | Current: {data.get('PACK_CURRENT', '-')} A")
    print(f"SOC: {data.get('SOC', '-')} %")
    
    temps = get_signals_by_type(data, 'temperature')
    temp_str = ', '.join([f"T{i+1}={t['value']}{t['unit']}" for i, t in enumerate(temps)])
    print(f"Temps: {temp_str}")
    
    discharge_status = data.get('FET_DISCHARGE_ACTIVE', '-')
    charge_status = data.get('FET_CHARGE_ACTIVE', '-')
    print(f"FETs: Charge={charge_status}, Discharge={discharge_status}")
    
    print(f"Min/Max Cell: {data.get('CELL_V_MIN', '-')} V / {data.get('CELL_V_MAX', '-')} V")
    
    if args.detailed_cells:
        cell_voltages = get_signals_by_type(data, 'cell_voltage')
        if cell_voltages:
            print(f"--- Individual Cell Groups (13S4P) ---")
            line = []
            for i, c in enumerate(cell_voltages):
                line.append(f"C{i+1:02d}: {c['value']:.3f} V")
                if (i + 1) % 4 == 0:
                    print(" | ".join(line))
                    line = []
            if line:
                 print(" | ".join(line))


def monitor_tui_fancy(data: dict, args: argparse.Namespace):
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\033[H\033[J", end="")
    print("--- BMS Live Monitoring (ePF-2 PRO) ---")
    print(f"Time: {data['TIMESTAMP']} | CMD: {data['COMMAND_ID']}")
    print("-" * 35)
    
    status_line = (
        f" SOC: \033[1;33m{data.get('SOC', '-'):<5} %\033[0m" # |"
        #f" Checksum: {'PASS' if data['CHECKSUM_VALIDITY'].startswith('PASS') else '\033[91mFAIL\033[0m'}"
    )
    print(status_line)
    
    V_pack = data.get('PACK_VOLTAGE', '-')
    I_pack = data.get('PACK_CURRENT', 0)
    
    print(f" V_Pack: \033[1;36m{V_pack:<8} V\033[0m")
    
    charge_status_str = data.get('FET_CHARGE_ACTIVE', 'Unknown')
    is_charging = charge_status_str == 'Active'
    current_color = '\033[92m' if is_charging else '\033[97m'
    #current_direction = 'Charging' if is_charging else 'Discharging'
    print(f" I_Pack: {current_color}{I_pack:<8} A") #\033[0m ({current_direction})")
    
    print("\n--- Temperature Sensors ---")
    temps = get_signals_by_type(data, 'temperature')
    temp_color = '\033[95m'
    temp_output = ' | '.join([f"T{i+1}: {t['value']}{t['unit']}" for i, t in enumerate(temps)])
    print(f" {temp_color}{temp_output}\033[0m")
    
    print("\n--- Operational Status ---")
    discharge_status_str = data.get('FET_DISCHARGE_ACTIVE', 'Unknown')
    
    charge_color = '\033[92m' if is_charging else '\033[90m'
    discharge_color = '\033[93m' if discharge_status_str == 'Active' else '\033[90m'
    
    print(f" {charge_color}CHARGE FET: {charge_status_str}\033[0m")
    print(f" {discharge_color}DISCHARGE FET: {discharge_status_str}\033[0m")
    
    print("\n--- Cell Health ---")
    print(f" Min Cell V: {data.get('CELL_V_MIN', '-')}")
    print(f" Max Cell V: {data.get('CELL_V_MAX', '-')}")
    
    if args.detailed_cells:
        cell_voltages = get_signals_by_type(data, 'cell_voltage')
        if cell_voltages:
            print("\n--- Individual Cell Groups (13S4P) ---")
            line = []
            for i, c in enumerate(cell_voltages):
                line.append(f"C{i+1:02d}: {c['value']:.3f} V")
                if (i + 1) % 4 == 0:
                    print(" | ".join(line))
                    line = []
            if line:
                 print()

def read_serial_loop(ser: serial.Serial, args: argparse.Namespace):
    logger = None
    log_enabled = args.log or args.log_type
    
    if log_enabled:
        log_types = []
        if args.log_type:
            if args.log_type == 'both':
                log_types = ['csv', 'json']
            else:
                log_types = [args.log_type]
        else:
            log_types = ['csv']
            
        logger = BMSSerialLogger(args.log_prefix, log_types)

    monitor_func = None
    if args.fancy:
        monitor_func = monitor_tui_fancy
    elif args.monitor:
        monitor_func = monitor_plain
        
    print(f"Starting monitoring on {ser.port}...")
    read_buffer = b''
    frame_start = TRANSPORT_LAYER_SPEC['FRAME_START']
    frame_length = TRANSPORT_LAYER_SPEC['FRAME_LENGTH']
    
    while True:
        try:
            incoming_bytes = ser.read(ser.in_waiting or 1)
            read_buffer += incoming_bytes
            start_index = read_buffer.find(frame_start)
            
            if start_index != -1:
                read_buffer = read_buffer[start_index:]
                
                if len(read_buffer) >= frame_length:
                    frame = read_buffer[:frame_length]
                    read_buffer = read_buffer[frame_length:]
                    
                    decoded_data = decode_frame(frame, args.detailed_cells)
                    
                    if 'ERROR' not in decoded_data:
                        if monitor_func:
                            monitor_func(decoded_data, args)
                        if logger:
                            logger.log_csv(decoded_data)
                            logger.log_json(decoded_data)
                    elif args.monitor or args.fancy:
                        print(f"\n[ERROR] {decoded_data['ERROR']}")
            elif len(read_buffer) > frame_length * 2: 
                read_buffer = read_buffer[-frame_length:]
                
        except serial.SerialException as e:
            print(f"\n[CRITICAL SERIAL ERROR] {e}")
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
            break
        except Exception as e:
            print(f"\n[UNEXPECTED ERROR] {e}")
            time.sleep(1)

def main():
    SERIAL_CONF = TRANSPORT_LAYER_SPEC['SERIAL_CONFIG']
    parser = argparse.ArgumentParser(
        description="Monitor and log status data from the ePowerFun ePF-2 PRO BMS via UART (AT YOUR OWN RISK).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('-p', '--port', type=str, default=SERIAL_CONF['port'],
                        help=f"Serial port device (default: {SERIAL_CONF['port']})")
    parser.add_argument('-b', '--baudrate', type=int, default=SERIAL_CONF['baudrate'],
                        help=f"Baud rate (default: {SERIAL_CONF['baudrate']})")
    
    monitor_group = parser.add_mutually_exclusive_group()
    monitor_group.add_argument('-m', '--monitor', action='store_true',
                                help="Print human-readable output (simple console dump).")
    monitor_group.add_argument('-f', '--fancy', action='store_true',
                                help="Use fancy TUI-style live updating output (clears screen).")
    
    parser.add_argument('-l', '--log', action='store_true',
                        help="Enable logging (required unless --log-type is specified).")
    parser.add_argument('-t', '--log-type', type=str, choices=['csv', 'json', 'both'], default=None,
                        help="Specify log file output format. Options: csv, json, both. Default: csv (if logging is enabled).")
    parser.add_argument('--log-prefix', type=str, default='bms_data',
                        help="Prefix name for log files (will be timestamped).")
    parser.add_argument('-c', '--detailed-cells', action='store_true',
                        help="Log and monitor individual cell voltages (13 groups).")
    
    args = parser.parse_args()
    
    log_enabled = args.log or args.log_type
    
    if not (args.monitor or args.fancy or log_enabled):
        parser.print_help()
        return

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            bytesize=SERIAL_CONF['bytesize'],
            parity=SERIAL_CONF['parity'],
            stopbits=SERIAL_CONF['stopbits'],
            timeout=SERIAL_CONF['timeout']
        )
        read_serial_loop(ser, args)
        ser.close()
    except serial.SerialException as e:
        print(f"Failed to open serial port {args.port}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == '__main__':
    main()
