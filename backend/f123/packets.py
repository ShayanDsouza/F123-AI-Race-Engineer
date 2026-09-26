"""F1 23 (packet format 2023) UDP telemetry packet parsing.

Only the packets needed by the app are fully decoded:
  header, session, lap data, car telemetry, car status, car damage,
  session history, event (lightweight).

Reference: EA/Codemasters F1 23 UDP specification (packetFormat == 2023).
All packets are little-endian.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

PACKET_FORMAT = 2023
NUM_CARS = 22

# Packet IDs
PID_MOTION = 0
PID_SESSION = 1
PID_LAP_DATA = 2
PID_EVENT = 3
PID_PARTICIPANTS = 4
PID_CAR_SETUPS = 5
PID_CAR_TELEMETRY = 6
PID_CAR_STATUS = 7
PID_FINAL_CLASSIFICATION = 8
PID_LOBBY_INFO = 9
PID_CAR_DAMAGE = 10
PID_SESSION_HISTORY = 11
PID_TYRE_SETS = 12
PID_MOTION_EX = 13

# ---------------------------------------------------------------- header ----
HEADER_FMT = "<HBBBBBQfIIBB"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 29
assert HEADER_SIZE == 29


@dataclass
class PacketHeader:
    packet_format: int
    game_year: int
    major_version: int
    minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int


def parse_header(data: bytes) -> Optional[PacketHeader]:
    if len(data) < HEADER_SIZE:
        return None
    return PacketHeader(*struct.unpack_from(HEADER_FMT, data, 0))


# --------------------------------------------------------------- session ----
TRACK_NAMES = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Bahrain",
    4: "Barcelona", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
    8: "Hockenheim", 9: "Hungaroring", 10: "Spa", 11: "Monza",
    12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Austin",
    16: "Interlagos", 17: "Red Bull Ring", 18: "Sochi", 19: "Mexico City",
    20: "Baku", 21: "Bahrain Short", 22: "Silverstone Short",
    23: "Austin Short", 24: "Suzuka Short", 25: "Hanoi", 26: "Zandvoort",
    27: "Imola", 28: "Portimao", 29: "Jeddah", 30: "Miami",
    31: "Las Vegas", 32: "Losail",
}

# Alternate/short layouts. These share a name with a real circuit but not its
# geometry, so overlaying them on real telemetry is meaningless.
SHORT_LAYOUT_IDS = {21, 22, 23, 24}

# Map game track id -> FastF1 2023 event name (absent = not on 2023 calendar)
TRACK_TO_2023_EVENT = {
    0: "Australian Grand Prix", 3: "Bahrain Grand Prix",
    4: "Spanish Grand Prix", 5: "Monaco Grand Prix",
    6: "Canadian Grand Prix", 7: "British Grand Prix",
    9: "Hungarian Grand Prix", 10: "Belgian Grand Prix",
    11: "Italian Grand Prix", 12: "Singapore Grand Prix",
    13: "Japanese Grand Prix", 14: "Abu Dhabi Grand Prix",
    15: "United States Grand Prix", 16: "São Paulo Grand Prix",
    17: "Austrian Grand Prix", 19: "Mexico City Grand Prix",
    20: "Azerbaijan Grand Prix", 21: "Bahrain Grand Prix",
    22: "British Grand Prix", 23: "United States Grand Prix",
    24: "Japanese Grand Prix", 26: "Dutch Grand Prix",
    27: "Emilia Romagna Grand Prix", 29: "Saudi Arabian Grand Prix",
    30: "Miami Grand Prix", 31: "Las Vegas Grand Prix",
    32: "Qatar Grand Prix",
}

SESSION_TYPES = {
    0: "Unknown", 1: "FP1", 2: "FP2", 3: "FP3", 4: "Short Practice",
    5: "Q1", 6: "Q2", 7: "Q3", 8: "Short Qualifying", 9: "One-Shot Q",
    10: "Race", 11: "Race 2", 12: "Race 3", 13: "Time Trial",
}

WEATHER = {0: "Clear", 1: "Light cloud", 2: "Overcast", 3: "Light rain",
           4: "Heavy rain", 5: "Storm"}
WET_WEATHER_IDS = {3, 4, 5}


def is_wet(weather_name: Optional[str]) -> bool:
    return weather_name in {WEATHER[i] for i in WET_WEATHER_IDS}

SESSION_FMT = "<BbbBHBbBHH"  # first fields after header


@dataclass
class SessionData:
    weather: int
    track_temperature: int
    air_temperature: int
    total_laps: int
    track_length: int  # metres
    session_type: int
    track_id: int
    formula: int
    session_time_left: int
    session_duration: int

    @property
    def track_name(self) -> str:
        return TRACK_NAMES.get(self.track_id, f"Track {self.track_id}")

    @property
    def session_name(self) -> str:
        return SESSION_TYPES.get(self.session_type, "Unknown")


def parse_session(data: bytes) -> Optional[SessionData]:
    if len(data) < HEADER_SIZE + struct.calcsize(SESSION_FMT):
        return None
    return SessionData(*struct.unpack_from(SESSION_FMT, data, HEADER_SIZE))


# -------------------------------------------------------------- lap data ----
LAP_DATA_FMT = "<IIHBHBHHfffBBBBBBBBBBBBBBBHHB"
LAP_DATA_SIZE = struct.calcsize(LAP_DATA_FMT)  # 50 bytes per car
assert LAP_DATA_SIZE == 50


@dataclass
class LapData:
    last_lap_time_ms: int
    current_lap_time_ms: int
    sector1_time_ms: int
    sector1_minutes: int
    sector2_time_ms: int
    sector2_minutes: int
    delta_car_in_front_ms: int
    delta_race_leader_ms: int
    lap_distance: float          # metres, can be negative before the line
    total_distance: float
    safety_car_delta: float
    car_position: int
    current_lap_num: int
    pit_status: int              # 0 none, 1 pitting, 2 in pit area
    num_pit_stops: int
    sector: int                  # 0/1/2
    current_lap_invalid: int     # 1 = invalid
    penalties: int
    total_warnings: int
    corner_cutting_warnings: int
    unserved_drive_through: int
    unserved_stop_go: int
    grid_position: int
    driver_status: int           # 0 garage 1 flying 2 in lap 3 out lap 4 on track
    result_status: int
    pit_lane_timer_active: int
    pit_lane_time_in_lane_ms: int
    pit_stop_timer_ms: int
    pit_stop_should_serve_pen: int


def parse_lap_data(data: bytes, car_index: int) -> Optional[LapData]:
    offset = HEADER_SIZE + car_index * LAP_DATA_SIZE
    if len(data) < offset + LAP_DATA_SIZE:
        return None
    return LapData(*struct.unpack_from(LAP_DATA_FMT, data, offset))


# --------------------------------------------------------- car telemetry ----
CAR_TELEMETRY_FMT = "<HfffBbHBBH4H4B4BH4f4B"
CAR_TELEMETRY_SIZE = struct.calcsize(CAR_TELEMETRY_FMT)  # 60 bytes per car
assert CAR_TELEMETRY_SIZE == 60


@dataclass
class CarTelemetry:
    speed: int            # km/h
    throttle: float       # 0..1
    steer: float          # -1..1
    brake: float          # 0..1
    clutch: int
    gear: int             # -1 R, 0 N, 1..8
    engine_rpm: int
    drs: int              # 0/1
    rev_lights_percent: int
    brakes_temperature: tuple
    tyres_surface_temperature: tuple
    tyres_inner_temperature: tuple
    engine_temperature: int
    tyres_pressure: tuple
    surface_type: tuple


def parse_car_telemetry(data: bytes, car_index: int) -> Optional[CarTelemetry]:
    offset = HEADER_SIZE + car_index * CAR_TELEMETRY_SIZE
    if len(data) < offset + CAR_TELEMETRY_SIZE:
        return None
    v = struct.unpack_from(CAR_TELEMETRY_FMT, data, offset)
    return CarTelemetry(
        speed=v[0], throttle=v[1], steer=v[2], brake=v[3], clutch=v[4],
        gear=v[5], engine_rpm=v[6], drs=v[7], rev_lights_percent=v[8],
        # v[9] rev lights bit value
        brakes_temperature=v[10:14],
        tyres_surface_temperature=v[14:18],
        tyres_inner_temperature=v[18:22],
        engine_temperature=v[22],
        tyres_pressure=v[23:27],
        surface_type=v[27:31],
    )


# ------------------------------------------------------------- car status ----
CAR_STATUS_FMT = "<BBBBBfffHHBBHBBBbfffBfffB"
CAR_STATUS_SIZE = struct.calcsize(CAR_STATUS_FMT)  # 55 bytes per car
assert CAR_STATUS_SIZE == 55

TYRE_COMPOUND_VISUAL = {16: "Soft", 17: "Medium", 18: "Hard",
                        7: "Intermediate", 8: "Wet"}


@dataclass
class CarStatus:
    fuel_in_tank: float
    fuel_capacity: float
    fuel_remaining_laps: float
    drs_allowed: int
    actual_tyre_compound: int
    visual_tyre_compound: int
    tyres_age_laps: int
    ers_store_energy: float       # Joules, max 4e6
    ers_deploy_mode: int          # 0 none 1 medium 2 hotlap 3 overtake
    ers_deployed_this_lap: float

    @property
    def tyre_name(self) -> str:
        return TYRE_COMPOUND_VISUAL.get(self.visual_tyre_compound, "Unknown")


def parse_car_status(data: bytes, car_index: int) -> Optional[CarStatus]:
    offset = HEADER_SIZE + car_index * CAR_STATUS_SIZE
    if len(data) < offset + CAR_STATUS_SIZE:
        return None
    v = struct.unpack_from(CAR_STATUS_FMT, data, offset)
    return CarStatus(
        fuel_in_tank=v[5], fuel_capacity=v[6], fuel_remaining_laps=v[7],
        drs_allowed=v[11], actual_tyre_compound=v[13],
        visual_tyre_compound=v[14], tyres_age_laps=v[15],
        ers_store_energy=v[19], ers_deploy_mode=v[20],
        ers_deployed_this_lap=v[23],
    )


# ------------------------------------------------------------- car damage ----
CAR_DAMAGE_FMT = "<4f4B4B18B"
CAR_DAMAGE_SIZE = struct.calcsize(CAR_DAMAGE_FMT)  # 42 bytes per car
assert CAR_DAMAGE_SIZE == 42


@dataclass
class CarDamage:
    tyres_wear: tuple      # percent RL RR FL FR? spec order: RL RR FL FR
    tyres_damage: tuple
    front_left_wing_damage: int
    front_right_wing_damage: int
    rear_wing_damage: int


def parse_car_damage(data: bytes, car_index: int) -> Optional[CarDamage]:
    offset = HEADER_SIZE + car_index * CAR_DAMAGE_SIZE
    if len(data) < offset + CAR_DAMAGE_SIZE:
        return None
    v = struct.unpack_from(CAR_DAMAGE_FMT, data, offset)
    return CarDamage(
        tyres_wear=v[0:4], tyres_damage=v[4:8],
        front_left_wing_damage=v[12], front_right_wing_damage=v[13],
        rear_wing_damage=v[14],
    )


# ---------------------------------------------------------- participants ----
PARTICIPANT_FMT = "<BBBBBBB48sBBB"
PARTICIPANT_SIZE = struct.calcsize(PARTICIPANT_FMT)  # 58 bytes per car
assert PARTICIPANT_SIZE == 58


@dataclass
class Participant:
    ai_controlled: bool
    driver_id: int
    team_id: int
    race_number: int
    name: str


def parse_participants(data: bytes) -> Optional[list]:
    """Driver identities for every car index — this is what lets us label an
    AI opponent's recorded lap 'VERSTAPPEN' rather than 'car 3'."""
    if len(data) < HEADER_SIZE + 1:
        return None
    num_cars = struct.unpack_from("<B", data, HEADER_SIZE)[0]
    base = HEADER_SIZE + 1
    out = []
    for i in range(min(num_cars, NUM_CARS)):
        off = base + i * PARTICIPANT_SIZE
        if len(data) < off + PARTICIPANT_SIZE:
            break
        v = struct.unpack_from(PARTICIPANT_FMT, data, off)
        raw_name = v[7].split(b"\x00", 1)[0]
        try:
            name = raw_name.decode("utf-8").strip()
        except UnicodeDecodeError:
            name = raw_name.decode("latin-1", "replace").strip()
        out.append(Participant(
            ai_controlled=bool(v[0]), driver_id=v[1], team_id=v[3],
            race_number=v[5], name=name or f"Car {i + 1}"))
    return out


TEAM_NAMES = {
    0: "Mercedes", 1: "Ferrari", 2: "Red Bull Racing", 3: "Williams",
    4: "Aston Martin", 5: "Alpine", 6: "AlphaTauri", 7: "Haas",
    8: "McLaren", 9: "Alfa Romeo",
}


# --------------------------------------------------------- session history ----
LAP_HISTORY_FMT = "<IHBHBHBB"
LAP_HISTORY_SIZE = struct.calcsize(LAP_HISTORY_FMT)  # 14
assert LAP_HISTORY_SIZE == 14
SESSION_HISTORY_FIXED_FMT = "<BBBBBBB"  # carIdx, numLaps, numTyreStints, best* x4


@dataclass
class LapHistoryEntry:
    lap_time_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    valid: bool  # lap time valid bit


@dataclass
class SessionHistory:
    car_index: int
    num_laps: int
    laps: list  # list[LapHistoryEntry]


def parse_session_history(data: bytes) -> Optional[SessionHistory]:
    fixed_size = struct.calcsize(SESSION_HISTORY_FIXED_FMT)
    if len(data) < HEADER_SIZE + fixed_size:
        return None
    fixed = struct.unpack_from(SESSION_HISTORY_FIXED_FMT, data, HEADER_SIZE)
    car_idx, num_laps = fixed[0], fixed[1]
    laps = []
    base = HEADER_SIZE + fixed_size
    for i in range(min(num_laps, 100)):
        off = base + i * LAP_HISTORY_SIZE
        if len(data) < off + LAP_HISTORY_SIZE:
            break
        (lap_ms, s1, s1m, s2, s2m, s3, s3m, flags) = struct.unpack_from(
            LAP_HISTORY_FMT, data, off)
        laps.append(LapHistoryEntry(
            lap_time_ms=lap_ms,
            sector1_ms=s1 + 60000 * s1m,
            sector2_ms=s2 + 60000 * s2m,
            sector3_ms=s3 + 60000 * s3m,
            valid=bool(flags & 0x01),
        ))
    return SessionHistory(car_index=car_idx, num_laps=num_laps, laps=laps)
