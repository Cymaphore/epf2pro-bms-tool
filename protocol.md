## BMS Protocol Specification (ePF-2 PRO 653Wh)

This script is the result of reverse engineering a proprietary battery protocol
of the "ePowerFun ePF-2 PRO 653Wh" battery (type FM-1304-3400-RS1 according
to the label).

It was obtained using PicoScope and analysis of the raw data, so there is
absolutely no guarantee about it being correct

Batteries pose inherent risks (fire, explosion, etc.). Use this information at your
own risk. Some of the information is currently speculative and must be validated.

### 1. Communication & Frame Structure

Physically, the BMS can be monitored from pin "B" against pin "-" on the battery.
It has a logic level of 3.3V.

| Parameter | Value | Notes |
| :---: | :---: | :---: |
| **BMS Model** | Proprietary, maybe JBD/LLT variant? | Not yet disassembled, purely guessed based on the data |
| **Baud Rate** | $9600\text{ bps}$ | Standard UART parameters (8N1). |
| **Frame Length** | 85 Bytes | Fixed length (Index 0 to 84). |
| **Header** | `EB 90` (Bytes 0-1) | Preamble/Start Bytes. |
| **Command IDs** | `A1 01` (Live Status), `A1 02` (Idle Status) | Located at Bytes 4-5. |
| **Payload Start** | Byte 6 (Bit 0) | All subsequent signal mapping uses **Bit Start** relative to this byte. |
| **Checksum Method** | **8-bit XOR Sum** | XOR summation of all bytes from Index 0 up to Index 83 (excluding the Checksum byte itself). |

What worked:

  * Using an TTL UART (RX) to read data
  * Actively writing to the BMS by connecting the TX pin over a schottky-diode to pin A (flow-direction towards the TX pin, so it can act as pull-down). However, since I have little knowledge about the protocol and yet need to monitor the communication between the controller and the BMS, I purely worked with the status message described below.
  
When the BMS is awake, the status message appears in 5s intervals. The BMS can be waken up by multiple means (for example connecting the charger, etc.) and stops when
the BMS goes to sleep.

#### Connectors

##### Charging-Port for external Charger

Round "Lemew"-labeled connector, needs further investigation, inner conductor "+", outer conductor "-".

Nominal output ratings:

| Parameter | Value | Notes |
| :---: | :---: | :---: |
| **Output Voltage** | $54.6 V$ |  |
| **Output Current** | $3.0 A$ |  |

##### Scooter-Interface

4 prong roughly 6.3 x 1.6mm flat blade connector. Exact model not yet identified.

| Pin | Function | Notes |
| :---: | :---: | :---: |
| **+** | Load output (P) | Switchable thru protective circuits, needs further investigation |
| **A** | Charging input (P) | Switched thru FET |
| **B** | BMS-Bus | 3.3V |
| **-** | Load output (N) / charging input (N) / GND  | Shared for all other pins |

### 2. Signal Map (Payload Detail)

For now, all signals are interpreted as Big Endian. The `Bit Start` is relative to the payload section (starting at Byte 6 of the full frame).

| Signal Name | Bit Start | Length (Bits) | Scaling (Gain/Offset) | Unit | Description |
| :---: | :---: | :---: | :---: | :---: | :---: |
| `PACK_VOLTAGE` | 0 | 16 | Gain: $0.01$ | $V$ | Total battery pack voltage. |
| `PACK_CURRENT` | 16 | 16 | Gain: $0.1$ | $A$ | Battery current |
| `STATUS_FLAGS_0F_10` | 72 | 16 | Raw | Raw | Probably protection flags (OV/UV/OT/OC). Requires further decoding. |
| `SOC` | 128 | 8 | Gain: $1.0$ | $\%$ | State of Charge. |

#### Operational Status Bits

These 1-bit flags are all over the place in the payload, meaning is mostly guessed and needs further investigation.

| Signal Name | Bit Start | Length (Bits) | Value Map | Unit | Description |
| :---: | :---: | :---: | :---: | :---: | :---: |
| `FET_DISCHARGE_ACTIVE` | 98 | 1 | 0: Inactive, 1: Active | Status | Maybe discharge FET status? |
| `FET_CHARGE_ACTIVE` | 100 | 1 | 0: Inactive, 1: Active | Status | Maybe Charge FET status? |
| `CHARGE_PIN_A_ACTIVE` | 102 | 1 | 0: Inactive, 1: Active | Status | Maybe Secondary charging path indicator. |

#### Temperature Sensors

All temperatures are 8-bit signed values providing Celsius degrees directly.

| Signal Name | Bit Start | Length (Bits) | Scaling (Gain/Offset) | Unit | Notes |
| :---: | :---: | :---: | :---: | :---: | :---: |
| `TEMP_1` | 592 | 8 | Signed, Gain: $1.0$ | $^\circ\text{C}$ | Tends to get warmer during charging, probably nearby BMS or some FET |
| `TEMP_2` | 600 | 8 | Signed, Gain: $1.0$ | $^\circ\text{C}$ | Internal cell area sensor. |
| `TEMP_3` | 608 | 8 | Signed, Gain: $1.0$ | $^\circ\text{C}$ | Internal cell area sensor. |
| `TEMP_4` | 616 | 8 | Signed, Gain: $1.0$ | $^\circ\text{C}$ | Potentially ambient or pack interior or something, maybe third cell level sensor |

### 3. Cell Group Voltages (13S4P Configuration)

The battery has 13 series-connected cell groups, each consisting of 4 parallel cells (4P). The block starts at Payload Bit 376. Each group voltage is recorded as an independent 16-bit signal.

| Signal Name | Bit Start | Length (Bits) | Scaling (Gain/Offset) | Unit |
| :---: | :---: | :---: | :---: | :---: |
| `CELL_01_V` | 376 | 16 | Gain: $0.001$ | $V$ |
| `CELL_02_V` | 392 | 16 | Gain: $0.001$ | $V$ |
| `CELL_03_V` | 408 | 16 | Gain: $0.001$ | $V$ |
| `CELL_04_V` | 424 | 16 | Gain: $0.001$ | $V$ |
| `CELL_05_V` | 440 | 16 | Gain: $0.001$ | $V$ |
| `CELL_06_V` | 456 | 16 | Gain: $0.001$ | $V$ |
| `CELL_07_V` | 472 | 16 | Gain: $0.001$ | $V$ |
| `CELL_08_V` | 488 | 16 | Gain: $0.001$ | $V$ |
| `CELL_09_V` | 504 | 16 | Gain: $0.001$ | $V$ |
| `CELL_10_V` | 520 | 16 | Gain: $0.001$ | $V$ |
| `CELL_11_V` | 536 | 16 | Gain: $0.001$ | $V$ |
| `CELL_12_V` | 552 | 16 | Gain: $0.001$ | $V$ |
| `CELL_13_V` | 568 | 16 | Gain: $0.001$ | $V$ |
