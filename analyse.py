#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import argparse
import sys
import datetime
import os
from typing import Dict, Any, List, Optional
from scipy.interpolate import interp1d

try:
    import matplotlib.pyplot as plt
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    print("Warning: matplotlib not found. Plotting features will be disabled.")

def setup_config() -> Dict[str, Any]:
    return {
        "NOMINAL_VOLTAGE_V": 48.0,
        "NOMINAL_CAPACITY_AH": 13.6,
        "NOMINAL_ENERGY_WH": 652.8,
        "TOTAL_CELLS": 52,
        "SERIES_CELLS": 13,
        "PARALLEL_CELLS": 4,
        "CELL_VOLTAGE_COLS": [f'CELL_{i:02d}_V' for i in range(1, 14)],
        # Minimum current change for Ri analysis
        "RI_PULSE_THRESHOLD_A": 2.0,
        # Current threshold for IU end criteria (conservative value / ePF-charger goes green at approx. 300mA)
        "CAP_CHARGE_END_A": 0.5,
        # Max current allowed to consider a point OCV stable for V-SoC calculation
        "OCV_CURRENT_THRESHOLD_A": 0.2,
        
        # Typical OCV vs. SoC Map for NMC/NCA (V per cell / 1S group)
        "OCV_SOC_MAP": {
            3.00: 0.0, 3.20: 5.0, 3.30: 10.0, 3.40: 20.0, 3.50: 50.0,
            3.60: 70.0, 3.80: 90.0, 4.00: 95.0, 4.10: 98.0, 4.20: 100.0
        }
    }

def calculate_v_soc(v_ocv_avg: float, config: Dict[str, Any]) -> float:
    ocv_map = config["OCV_SOC_MAP"]
    v_points = sorted(ocv_map.keys())
    soc_points = [ocv_map[v] for v in v_points]
    
    if v_ocv_avg < v_points[0]: return 0.0
    if v_ocv_avg > v_points[-1]: return 100.0
        
    f = interp1d(v_points, soc_points, kind='linear')
    return float(f(v_ocv_avg))

def load_data(file_path: str, config: Dict[str, Any]) -> pd.DataFrame:
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        sys.exit(1)
        
    if any(col not in df.columns for col in config['CELL_VOLTAGE_COLS']):
        print(f"Error: Missing expected cell voltage columns.")
        sys.exit(1)
        
    df['Timestamp'] = pd.to_datetime(df['TIMESTAMP'])
    df['Time_Sec'] = (df['Timestamp'] - df['Timestamp'].iloc[0]).dt.total_seconds()
    df['Delta_T'] = df['Time_Sec'].diff().fillna(0)

    # Guess the current direction, until I figure out the bit in the bms protocol
    df['V_Diff'] = df['PACK_VOLTAGE'].diff().fillna(0)
    df['Current_Signed'] = df['PACK_CURRENT']
    discharge_mask = (df['PACK_CURRENT'] > 0.1) & (df['V_Diff'] < -0.01)
    df.loc[discharge_mask, 'Current_Signed'] = -df.loc[discharge_mask, 'PACK_CURRENT']
    
    df['V_Group_Avg'] = df[config['CELL_VOLTAGE_COLS']].mean(axis=1)
    rest_mask = df['PACK_CURRENT'].abs() < config['OCV_CURRENT_THRESHOLD_A']
    df['OCV_V'] = np.nan
    df.loc[rest_mask, 'OCV_V'] = df.loc[rest_mask, 'V_Group_Avg']
    df['V_SoC'] = df['OCV_V'].apply(lambda x: calculate_v_soc(x, config) if pd.notna(x) else np.nan)
    
    if 'TEMP_1' in df.columns:
        df = df.drop(columns=['TEMP_1'])

    return df

def _save_plot(fig: plt.Figure, title: str, prefix: str):
    if not PLOTTING_AVAILABLE:
        return
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{title}_{timestamp}.png"
    
    filename = filename.replace(' ', '_').replace('/', '_').replace('-', '_').lower()
    
    try:
        fig.savefig(filename, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"-> Saved plot: {filename}")
    except Exception as e:
        print(f"Error saving plot {filename}: {e}")

def analyze_ri(df: pd.DataFrame, config: Dict[str, Any], plot_prefix: Optional[str]):
    
    print("\n\n--- Ri (Internal Resistance) Analysis during Discharge Pulses ---")
    
    Ri_results: List[Dict[str, Any]] = []
    
    df['Current_Abs_Diff'] = df['Current_Signed'].diff().abs()
    pulse_start_indices = df[(df['Current_Abs_Diff'] > config['RI_PULSE_THRESHOLD_A']) & (df['Current_Signed'] < 0)].index
    temp_cols = [t for t in ['TEMP_2', 'TEMP_3', 'TEMP_4'] if t in df.columns]

    for start_idx in pulse_start_indices:
        if start_idx < 1 or df.loc[start_idx-1, 'PACK_CURRENT'] > config['OCV_CURRENT_THRESHOLD_A']:
            continue
            
        I_pulse = -df.loc[start_idx, 'Current_Signed']
        V_pre_pack = df.loc[start_idx-1, 'PACK_VOLTAGE']
        V_pulse_pack = df.loc[start_idx, 'PACK_VOLTAGE']
        Ri_pack = (V_pre_pack - V_pulse_pack) / I_pulse
        
        temp_data = {t: df.loc[start_idx, t] for t in temp_cols}
        v_soc_pre = df.loc[start_idx-1, 'V_SoC'] if pd.notna(df.loc[start_idx-1, 'V_SoC']) else np.nan
        
        result = {
            'Timestamp': df.loc[start_idx, 'Timestamp'],
            'I_Pulse_A': I_pulse,
            'T_Avg_C': np.mean(list(temp_data.values())),
            'Ri_Pack_mOhm': Ri_pack * 1000,
            'SoC_BMS': df.loc[start_idx, 'SOC'],
            'SoC_OCV': v_soc_pre,
        }
        
        I_string = I_pulse / config['PARALLEL_CELLS']
        for i, col in enumerate(config['CELL_VOLTAGE_COLS']):
            V_pre_group = df.loc[start_idx-1, col]
            V_pulse_group = df.loc[start_idx, col]
            Ri_cell_equivalent = (V_pre_group - V_pulse_group) / I_string
            result[f'Ri_Cell_S{i+1}_mOhm'] = Ri_cell_equivalent * 1000
        
        Ri_results.append(result)
    
    if Ri_results:
        ri_df = pd.DataFrame(Ri_results)
        
        ri_cell_cols = [c for c in ri_df.columns if c.startswith('Ri_Cell')]
        
        if plot_prefix and PLOTTING_AVAILABLE:
            
            fig, ax1 = plt.subplots(figsize=(10, 6))
            fig.suptitle('Internal Resistance vs. Temperature and SoC', fontsize=14)
            
            ax1.set_xlabel('Timestamp')
            ax1.set_ylabel('Ri [mOhm]', color='tab:blue')
            ax1.plot(ri_df['Timestamp'], ri_df['Ri_Pack_mOhm'], marker='o', linestyle='-', color='tab:blue', label='Pack Ri (mOhm)')
            ax1.tick_params(axis='y', labelcolor='tab:blue')
            
            ax2 = ax1.twinx()
            ax2.set_ylabel('Temp [°C] / SoC [%]', color='tab:red')
            ax2.plot(ri_df['Timestamp'], ri_df['T_Avg_C'], marker='x', linestyle='--', color='tab:red', label='Avg Temp (°C)')
            ax2.plot(ri_df['Timestamp'], ri_df['SoC_BMS'], marker='^', linestyle=':', color='tab:purple', label='BMS SoC (%)')
            ax2.plot(ri_df['Timestamp'], ri_df['SoC_OCV'], marker='v', linestyle=':', color='tab:green', label='OCV SoC (%)')
            ax2.tick_params(axis='y', labelcolor='tab:red')
            
            fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
            fig.autofmt_xdate()
            _save_plot(fig, 'ri_vs_temp_soc', plot_prefix)
            
            first_idx = pulse_start_indices[0]
            start = max(0, first_idx - 5)
            end = min(len(df), first_idx + 5)
            pulse_window = df.iloc[start:end].copy()
            
            fig, ax1 = plt.subplots(figsize=(10, 6))
            fig.suptitle(f'Detailed View of First Discharge Pulse @ {ri_df.iloc[0]["Timestamp"].time()}', fontsize=14)
            
            ax1.set_xlabel('Timestamp')
            ax1.set_ylabel('Pack Voltage [V]', color='tab:blue')
            ax1.plot(pulse_window['Timestamp'], pulse_window['PACK_VOLTAGE'], marker='o', color='tab:blue', label='Pack Voltage')
            ax1.tick_params(axis='y', labelcolor='tab:blue')
            
            ax2 = ax1.twinx()
            ax2.set_ylabel('Current [A]', color='tab:red')
            ax2.plot(pulse_window['Timestamp'], pulse_window['Current_Signed'], marker='x', color='tab:red', label='Current (Signed)')
            ax2.tick_params(axis='y', labelcolor='tab:red')

            ax1.axvline(x=ri_df.iloc[0]['Timestamp'], color='gray', linestyle='--', label='Pulse Application')
            
            fig.legend(loc="upper right", bbox_to_anchor=(0.9, 0.9))
            fig.autofmt_xdate()
            _save_plot(fig, 'ri_pulse_detail', plot_prefix)

        ri_df = pd.DataFrame(Ri_results)
        for col in ri_df.columns:
            if 'mOhm' in col or 'A' in col or 'C' in col or 'SoC' in col:
                ri_df[col] = ri_df[col].round(2)
        print(ri_df.to_string(index=False))
        
    else:
        print("No significant discharge pulses found based on the Ri threshold (Current must be near zero before pulse).")

def analyze_capacity(df: pd.DataFrame, config: Dict[str, Any], plot_prefix: Optional[str]):
    
    print("\n\n--- Charge Capacity Analysis ---")
    
    charge_df = df[df['Current_Signed'] > 0].copy()
    
    if charge_df.empty:
        print("No continuous charging events found in the dataset.")
        return

    charge_df['dAh'] = charge_df['Current_Signed'] * charge_df['Delta_T'] / 3600
    charge_df['Accumulated_Ah'] = charge_df['dAh'].cumsum()
    charge_df['dWh'] = charge_df['Current_Signed'] * charge_df['PACK_VOLTAGE'] * charge_df['Delta_T'] / 3600
    charge_df['Accumulated_Wh'] = charge_df['dWh'].cumsum()

    if plot_prefix and PLOTTING_AVAILABLE:
        
        fig, ax1 = plt.subplots(figsize=(12, 6))
        fig.suptitle('Charge Cycle Profile: Ah, Wh, and Current', fontsize=14)
        
        ax1.set_xlabel('Time [s]')
        ax1.set_ylabel('Accumulated Charge/Energy', color='tab:blue')
        ax1.plot(charge_df['Time_Sec'], charge_df['Accumulated_Ah'], color='tab:blue', label='Accumulated Ah')
        ax1.plot(charge_df['Time_Sec'], charge_df['Accumulated_Wh'] / config['NOMINAL_VOLTAGE_V'], linestyle='--', color='tab:cyan', label='Accumulated Wh (Scaled)')
        ax1.tick_params(axis='y', labelcolor='tab:blue')
        
        ax2 = ax1.twinx()
        ax2.set_ylabel('Current [A]', color='tab:red')
        ax2.plot(charge_df['Time_Sec'], charge_df['Current_Signed'], color='tab:red', label='Current (A)')
        ax2.tick_params(axis='y', labelcolor='tab:red')

        fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
        _save_plot(fig, 'capacity_charge_profile', plot_prefix)
        
        fig, ax1 = plt.subplots(figsize=(12, 6))
        fig.suptitle('Pack Voltage and Imbalance during Charge', fontsize=14)

        v_cols = config['CELL_VOLTAGE_COLS']
        charge_df['V_Range'] = charge_df[v_cols].max(axis=1) - charge_df[v_cols].min(axis=1)
        
        ax1.set_xlabel('BMS SoC [%]')
        ax1.set_ylabel('Pack Voltage [V]', color='tab:blue')
        ax1.plot(charge_df['SOC'], charge_df['PACK_VOLTAGE'], color='tab:blue', label='Pack Voltage')
        ax1.tick_params(axis='y', labelcolor='tab:blue')
        
        ax2 = ax1.twinx()
        ax2.set_ylabel('Group Voltage Range [V]', color='tab:red')
        ax2.plot(charge_df['SOC'], charge_df['V_Range'], color='tab:red', label='V_Max - V_Min')
        ax2.tick_params(axis='y', labelcolor='tab:red')

        fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
        _save_plot(fig, 'capacity_v_vs_soc_imbalance', plot_prefix)
    
    idx_stop = charge_df.index[-1]
    Ah_log = charge_df.loc[idx_stop, 'Accumulated_Ah']
    Wh_log = charge_df.loc[idx_stop, 'Accumulated_Wh']
    time_sec = charge_df.loc[idx_stop, 'Time_Sec'] - charge_df.iloc[0]['Time_Sec']
    
    results = {'Log_End': (Ah_log, Wh_log, str(datetime.timedelta(seconds=time_sec)))}
    start_soc_bms = charge_df['SOC'].iloc[0]
    end_soc_bms = charge_df['SOC'].iloc[-1]
    start_soc_ocv = charge_df['V_SoC'].iloc[0] if pd.notna(charge_df['V_SoC'].iloc[0]) else np.nan
    end_soc_ocv = charge_df['V_SoC'].iloc[-1] if pd.notna(charge_df['V_SoC'].iloc[-1]) else np.nan
    
    print(f"\nNominal Capacity: {config['NOMINAL_CAPACITY_AH']} Ah | Nominal Energy: {config['NOMINAL_ENERGY_WH']} Wh")
    
    print("\n--- Capacity Calculation Summary ---")
    for label, (ah, wh, time_str) in results.items():
        print(f"\n[Result: {label}]")
        print(f"| Accumulated Ah: {ah:8.3f} Ah")
        print(f"| Accumulated Wh: {wh:8.3f} Wh")
        print(f"| Total Time: {time_str}")

    print("\n--- State of Charge Validation ---")
    print(f"| BMS Reported SoC: Start {start_soc_bms:.1f}% -> End {end_soc_bms:.1f}% (Delta: {(end_soc_bms - start_soc_bms):.1f}%)")
    
    if pd.notna(start_soc_ocv) and pd.notna(end_soc_ocv):
        ocv_delta = end_soc_ocv - start_soc_ocv
        print(f"| OCV Inferred SoC: Start {start_soc_ocv:.1f}% -> End {end_soc_ocv:.1f}% (Delta: {ocv_delta:.1f}%)")
        
        if ocv_delta > 0.01:
            implied_nominal_cap = (Ah_log / ocv_delta) * 100
            print(f"| Implied 100% Ah Capacity (via OCV): {implied_nominal_cap:8.3f} Ah")
        
    if charge_df.shape[0] > 10:
        print("\nCell Group Behavior Check (End of Log):")
        cell_range = charge_df.loc[idx_stop, 'V_Range']
        print(f"  Group Voltage Range (Max - Min): {cell_range:.3f} V")
        if cell_range > 0.05:
            print("  -> Warning: Significant voltage range suggests balancing is needed or capacity variance is high.")
        else:
            print("  -> Series group voltages are well balanced.")


def analyze_standby(df: pd.DataFrame, config: Dict[str, Any], plot_prefix: Optional[str]):
    
    print("\n\n--- Standby/Rest Period Analysis ---")
    
    standby_df = df[df['PACK_CURRENT'].abs() < config['OCV_CURRENT_THRESHOLD_A']].copy()
    
    if standby_df.empty:
        print(f"No valid standby periods found (Current must be < {config['OCV_CURRENT_THRESHOLD_A']} A).")
        return
    
    v_cols = config['CELL_VOLTAGE_COLS']
    standby_df['V_Range'] = standby_df[v_cols].max(axis=1) - standby_df[v_cols].min(axis=1)

    duration_sec = standby_df['Time_Sec'].iloc[-1] - standby_df['Time_Sec'].iloc[0]
    print(f"Analyzed {standby_df.shape[0]} rest samples over {str(datetime.timedelta(seconds=duration_sec))}.")

    temp_cols = [t for t in ['TEMP_2', 'TEMP_3', 'TEMP_4'] if t in df.columns]
    
    if temp_cols:
        T_start = standby_df[temp_cols].iloc[0].mean()
        T_end = standby_df[temp_cols].iloc[-1].mean()
        print(f"\nTemperature (Avg T2-T4): Start {T_start:.1f} °C, End {T_end:.1f} °C (Change: {T_end - T_start:.1f} °C)")

    V_Range_max = standby_df['V_Range'].max()
    print(f"\nVoltage Imbalance Summary (Max V_Group - Min V_Group):")
    print(f"  Max observed range: {V_Range_max:.3f} V")
    
    if V_Range_max > 0.05:
        print("  -> Imbalance is significant. Balancing may be active or groups differ in capacity/Ri.")
    
    bms_soc_start = standby_df['SOC'].iloc[0]
    bms_soc_end = standby_df['SOC'].iloc[-1]
    ocv_soc_start = standby_df['V_SoC'].iloc[0]
    ocv_soc_end = standby_df['V_SoC'].iloc[-1]
    
    print("\nSoC Comparison:")
    print(f"| BMS Reported SoC: {bms_soc_start:.1f}% -> {bms_soc_end:.1f}% (Delta: {bms_soc_end - bms_soc_start:.1f}%)")
    
    if pd.notna(ocv_soc_start) and pd.notna(ocv_soc_end):
        ocv_offset_start = bms_soc_start - ocv_soc_start
        ocv_offset_end = bms_soc_end - ocv_soc_end
        print(f"| OCV Inferred SoC: {ocv_soc_start:.1f}% -> {ocv_soc_end:.1f}% (Delta: {ocv_soc_end - ocv_soc_start:.1f}%)")
        print(f"| BMS Offset (Start/End): {ocv_offset_start:.1f}% / {ocv_offset_end:.1f}%")
        
        if abs(ocv_offset_start) > 5 or abs(ocv_offset_end) > 5:
            print("  -> Warning: BMS SoC significantly deviates from OCV-based estimate. Recalibration needed.")

    if plot_prefix and PLOTTING_AVAILABLE:
        
        fig, ax1 = plt.subplots(figsize=(12, 6))
        fig.suptitle('Standby Analysis: Cell Group Voltages and Imbalance', fontsize=14)
        
        ax1.set_xlabel('Time [s]')
        ax1.set_ylabel('Group Voltage [V]')
        
        standby_df.set_index('Time_Sec')[v_cols].plot(ax=ax1, legend=False, color='gray', alpha=0.3)
        
        ax2 = ax1.twinx()
        ax2.set_ylabel('Voltage Range (Imbalance) [V]', color='tab:red')
        ax2.plot(standby_df['Time_Sec'], standby_df['V_Range'], color='tab:red', linewidth=2, label='V_Max - V_Min')
        ax2.tick_params(axis='y', labelcolor='tab:red')

        fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
        _save_plot(fig, 'standby_voltage_imbalance', plot_prefix)
        
        fig, ax = plt.subplots(figsize=(12, 6))
        fig.suptitle('Standby Analysis: SoC Comparison (BMS vs. OCV)', fontsize=14)
        
        ax.set_xlabel('Time [s]')
        ax.set_ylabel('State of Charge [%]')
        ax.plot(standby_df['Time_Sec'], standby_df['SOC'], label='BMS Reported SoC', color='tab:blue', marker='.')
        ax.plot(standby_df['Time_Sec'], standby_df['V_SoC'], label='OCV Inferred SoC', color='tab:green', marker='.')
        
        ax.legend()
        _save_plot(fig, 'standby_soc_comparison', plot_prefix)


def main():
    config = setup_config()
    
    parser = argparse.ArgumentParser(
        description="BMS Data Analyzer for Ri, Capacity, and Standby Diagnostics.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("file_path", nargs='?', help="Path to the BMS CSV file.")
    parser.add_argument("--ri_analysis", action="store_true", help="Perform Ri analysis during discharge pulses.")
    parser.add_argument("--capacity_analysis", action="store_true", help="Perform capacity determination during charge cycles (Ah, Wh, SoC check).")
    parser.add_argument("--standby_analysis", action="store_true", help="Analyze voltage variance, temperatures, and SoC comparison during rest periods.")
    parser.add_argument("--plot_prefix", type=str, default=None, help="If provided, saves generated diagrams using this prefix (e.g., --plot_prefix run_1). Requires matplotlib.")
    
    args = parser.parse_args()
    
    if args.file_path is None:
        parser.print_help()
        sys.exit(0)
    
    df = load_data(args.file_path, config)

    if args.ri_analysis:
        analyze_ri(df, config, args.plot_prefix)
        
    if args.capacity_analysis:
        analyze_capacity(df, config, args.plot_prefix)
        
    if args.standby_analysis:
        analyze_standby(df, config, args.plot_prefix)

if __name__ == "__main__":
    main()
