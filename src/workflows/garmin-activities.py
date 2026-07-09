from datetime import datetime, UTC, timedelta

import time
import json
import urllib.parse

import pytz
from dotenv import load_dotenv
from garminconnect import Garmin as GarminClient
from notion_client import Client as NotionClient

from src.helpers import get_garmin_client, get_notion_client

# Your local time zone, replace with the appropriate one if needed
local_tz = pytz.timezone('Europe/London')

ACTIVITY_ICONS = {
    "Barre": "https://img.icons8.com/?size=100&id=66924&format=png&color=000000",
    "Breathwork": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Cardio": "https://img.icons8.com/?size=100&id=71221&format=png&color=000000",
    "Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Hiking": "https://img.icons8.com/?size=100&id=9844&format=png&color=000000",
    "Indoor Cardio": "https://img.icons8.com/?size=100&id=62779&format=png&color=000000",
    "Indoor Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Indoor Rowing": "https://img.icons8.com/?size=100&id=71098&format=png&color=000000",
    "Pilates": "https://img.icons8.com/?size=100&id=9774&format=png&color=000000",
    "Meditation": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Rowing": "https://img.icons8.com/?size=100&id=71491&format=png&color=000000",
    "Running": "https://img.icons8.com/?size=100&id=k1l1XFkME39t&format=png&color=000000",
    "Strength Training": "https://img.icons8.com/?size=100&id=107640&format=png&color=000000",
    "Stretching": "https://img.icons8.com/?size=100&id=djfOcRn1m_kh&format=png&color=000000",
    "Swimming": "https://img.icons8.com/?size=100&id=9777&format=png&color=000000",
    "Treadmill Running": "https://img.icons8.com/?size=100&id=9794&format=png&color=000000",
    "Walking": "https://img.icons8.com/?size=100&id=9807&format=png&color=000000",
    "Yoga": "https://img.icons8.com/?size=100&id=9783&format=png&color=000000",
    # Add more mappings as needed
}


def get_all_activities(garmin_client: GarminClient, limit: int = 1000) -> list[dict]:
    return garmin_client.get_activities(0, limit)


def format_activity_type(activity_type: str, activity_name: str = "") -> tuple[str, str]:
    # First format the activity type as before
    formatted_type = activity_type.replace('_', ' ').title() if activity_type else "Unknown"

    # Initialize subtype as the same as the main type
    activity_subtype = formatted_type
    activity_type = formatted_type

    # Map of specific subtypes to their main types
    activity_mapping = {
        "Barre": "Strength",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength",
        "Treadmill Running": "Running"
    }

    # Special replacement for Rowing V2
    if formatted_type == "Rowing V2":
        activity_type = "Rowing"

    # Special case for Yoga and Pilates
    elif formatted_type in ["Yoga", "Pilates"]:
        activity_type = "Yoga/Pilates"
        activity_subtype = formatted_type

    # If the formatted type is in our mapping, update both main type and subtype
    if formatted_type in activity_mapping:
        activity_type = activity_mapping[formatted_type]
        activity_subtype = formatted_type

    # Special cases for activity names
    if activity_name and "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if activity_name and "barre" in activity_name.lower():
        return "Strength", "Barre"
    if activity_name and "stretch" in activity_name.lower():
        return "Stretching", "Stretching"

    return activity_type, activity_subtype


def format_entertainment(activity_name: str) -> str:
    return activity_name.replace('ENTERTAINMENT', 'Netflix')


def format_training_message(message: str) -> str:
    messages = {
        'NO_': 'No Benefit',
        'MINOR_': 'Some Benefit',
        'RECOVERY_': 'Recovery',
        'MAINTAINING_': 'Maintaining',
        'IMPROVING_': 'Impacting',
        'IMPACTING_': 'Impacting',
        'HIGHLY_': 'Highly Impacting',
        'OVERREACHING_': 'Overreaching'
    }
    for key, value in messages.items():
        if message.startswith(key):
            return value
    return message


def format_training_effect(training_effect_label: str) -> str:
    return training_effect_label.replace('_', ' ').title()


def format_pace(average_speed: float) -> str:
    if average_speed > 0:
        pace_min_km = 1000 / (average_speed * 60)  # Convert to min/km
        minutes = int(pace_min_km)
        seconds = int((pace_min_km - minutes) * 60)
        return f"{minutes}:{seconds:02d} min/km"
    else:
        return ""


def activity_exists(
    notion_client: NotionClient,
    database_id: str,
    activity_date: datetime,
    activity_type: str,
    activity_name: str,
) -> dict | None:
    # Check if an activity already exists in the Notion database and return it if found.

    # Determine the correct activity type for the lookup
    lookup_type = "Stretching" if "stretch" in activity_name.lower() else activity_type

    # Create a time window to search for the activity. Notion has been observed to truncate datetimes to the minutes in
    # some instances, causing the lookup using exact datetime to fail.
    # TODO: We should store the activity ID in the Notion page to avoid this complexity.
    lookup_min_date = activity_date - timedelta(minutes=5)
    lookup_max_date = activity_date + timedelta(minutes=5)

    query = notion_client.databases.query(
        database_id=database_id,
        filter={
            "and": [
                {"property": "Date", "date": {"on_or_after": lookup_min_date.isoformat()}},
                {"property": "Date", "date": {"on_or_before": lookup_max_date.isoformat()}},
                # NOTE: Activity Type filter removed on purpose. Filtering a select by a value that
                # isn't already an existing option returns a 400 error. De-duplicating by the date
                # window + activity name is sufficient, and lets new activity types be created freely.
                {"property": "Activity Name", "title": {"equals": activity_name}}
            ]
        }
    )
    results = query['results']
    return results[0] if results else None


def activity_needs_update(existing_activity: dict, new_activity: dict) -> bool:
    existing_props = existing_activity['properties']

    activity_name = new_activity.get('activityName', '').lower()
    activity_type, activity_subtype = format_activity_type(
        new_activity.get('activityType', {}).get('typeKey', 'Unknown'),
        activity_name
    )

    # Check if 'Subactivity Type' property exists
    has_subactivity = (
        'Subactivity Type' in existing_props and
        existing_props['Subactivity Type'] is not None and
        existing_props['Subactivity Type'].get('select') is not None
    )

    return (
        existing_props['Distance (km)']['number'] != round(new_activity.get('distance', 0) / 1000, 2) or
        existing_props['Duration (min)']['number'] != round(new_activity.get('duration', 0) / 60, 2) or
        existing_props['Calories']['number'] != round(new_activity.get('calories', 0)) or
        (existing_props['Avg HR']['number'] or 0) != round(new_activity.get('averageHR') or 0) or
        (existing_props['Max HR']['number'] or 0) != round(new_activity.get('maxHR') or 0) or
        (existing_props['Avg Cadence']['number'] or 0) != round(new_activity.get('averageRunningCadenceInStepsPerMinute') or new_activity.get('averageBikingCadenceInRevPerMinute') or 0) or
        (existing_props['Elevation Gain (m)']['number'] or 0) != round(new_activity.get('elevationGain') or 0, 1) or
        (existing_props['Training Load']['number'] or 0) != round(new_activity.get('activityTrainingLoad') or 0, 1) or
        existing_props['Avg Pace']['rich_text'][0]['text']['content'] != format_pace(
            new_activity.get('averageSpeed', 0)
        ) or
        existing_props['Avg Power']['number'] != round(new_activity.get('avgPower', 0), 1) or
        existing_props['Max Power']['number'] != round(new_activity.get('maxPower', 0), 1) or
        existing_props['Training Effect']['select']['name'] != format_training_effect(
            new_activity.get('trainingEffectLabel', 'Unknown')
        ) or
        existing_props['Aerobic']['number'] != round(new_activity.get('aerobicTrainingEffect', 0), 1) or
        existing_props['Aerobic Effect']['select']['name'] != format_training_message(
            new_activity.get('aerobicTrainingEffectMessage', 'Unknown')
        ) or
        existing_props['Anaerobic']['number'] != round(new_activity.get('anaerobicTrainingEffect', 0), 1) or
        existing_props['Anaerobic Effect']['select']['name'] != format_training_message(
            new_activity.get('anaerobicTrainingEffectMessage', 'Unknown')
        ) or
        existing_props['PR']['checkbox'] != new_activity.get('pr', False) or
        existing_props['Fav']['checkbox'] != new_activity.get('favorite', False) or
        existing_props['Activity Type']['select']['name'] != activity_type or
        (has_subactivity and existing_props['Subactivity Type']['select']['name'] != activity_subtype) or
        (not has_subactivity)  # If the property doesn't exist, we need an update
    )


def _rt(text) -> list:
    # Build a Notion rich-text array from a plain string.
    return [{"type": "text", "text": {"content": str(text)}}]


def add_lap_data(notion_client: NotionClient, garmin_client: GarminClient, page_id: str, activity_id) -> str:
    # Fetch per-lap splits from Garmin and write them as a table on the activity's page.
    # Returns "added" (table written), "skip" (nothing worth adding) or "error" (retry next run).
    try:
        splits = garmin_client.get_activity_splits(activity_id)
    except Exception as e:
        print(f"  Could not fetch splits for activity {activity_id}: {e}")
        return "error"

    laps = (splits or {}).get('lapDTOs') or []
    if len(laps) < 2:
        return "skip"  # single-lap activity: no interval breakdown worth showing

    laps = laps[:90]  # Notion caps children per request; 90 keeps us safely under the limit
    header = ["Lap", "Dist (km)", "Time", "Pace", "Avg HR", "Max HR"]
    table_rows = [{"type": "table_row", "table_row": {"cells": [_rt(c) for c in header]}}]
    for i, lap in enumerate(laps, 1):
        dur_s = lap.get('duration') or 0
        row = [
            str(i),
            f"{round((lap.get('distance') or 0) / 1000, 2)}",
            f"{int(dur_s // 60)}:{int(dur_s % 60):02d}",
            format_pace(lap.get('averageSpeed') or 0),
            str(round(lap.get('averageHR') or 0)),
            str(round(lap.get('maxHR') or 0)),
        ]
        table_rows.append({"type": "table_row", "table_row": {"cells": [_rt(c) for c in row]}})

    children = [
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": _rt("🔁 Lap / interval breakdown")}},
        {"object": "block", "type": "table",
         "table": {
             "table_width": len(header),
             "has_column_header": True,
             "has_row_header": False,
             "children": table_rows,
         }},
    ]
    try:
        notion_client.blocks.children.append(block_id=page_id, children=children)
    except Exception as e:
        print(f"  Could not write lap table to page {page_id}: {e}")
        return "error"
    return "added"


def _rt_chunks(text) -> list:
    # Notion caps each rich-text item at 2000 characters, so split long strings into chunks.
    s = str(text)
    chunks = [{"type": "text", "text": {"content": s[i:i + 1900]}} for i in range(0, len(s), 1900)]
    return chunks or [{"type": "text", "text": {"content": ""}}]


def _parse_detail_series(details: dict) -> list:
    # Turn Garmin's get_activity_details payload into a simple list of {t, hr, spd} samples.
    descriptors = (details or {}).get('metricDescriptors') or []
    idx = {}
    for d in descriptors:
        key = d.get('key')
        if key is not None:
            idx[key] = d.get('metricsIndex')
    t_i = idx.get('sumElapsedDuration', idx.get('sumDuration'))
    hr_i = idx.get('directHeartRate')
    spd_i = idx.get('directSpeed')
    if t_i is None or hr_i is None or spd_i is None:
        return []
    samples = []
    for point in (details.get('activityDetailMetrics') or []):
        m = point.get('metrics') or []
        try:
            t, hr, spd = m[t_i], m[hr_i], m[spd_i]
        except (IndexError, TypeError):
            continue
        if t is None or hr is None or spd is None:
            continue
        samples.append({"t": float(t), "hr": float(hr), "spd": float(spd)})
    return samples


def _aerobic_decoupling(samples: list) -> float | None:
    # Aerobic decoupling %: how much speed-per-HR efficiency fades from the first half to the second half.
    valid = [s for s in samples if s['hr'] > 0 and s['spd'] > 0]
    if len(valid) < 10:
        return None
    mid = len(valid) // 2
    first, second = valid[:mid], valid[mid:]

    def efficiency(chunk: list) -> float:
        avg_hr = sum(s['hr'] for s in chunk) / len(chunk)
        avg_spd = sum(s['spd'] for s in chunk) / len(chunk)
        return (avg_spd / avg_hr) if avg_hr > 0 else 0.0

    ef1, ef2 = efficiency(first), efficiency(second)
    if ef1 <= 0:
        return None
    return round((ef1 - ef2) / ef1 * 100, 1)


def _build_chart_url(samples: list) -> str | None:
    # Build a QuickChart (Chart.js) line chart of HR and pace over time, embeddable as an image.
    if len(samples) < 5:
        return None
    step = max(1, len(samples) // 60)
    ds = samples[::step]
    labels = [round(s['t'] / 60, 1) for s in ds]  # minutes
    hr = [round(s['hr']) for s in ds]
    pace = [round(1000 / (s['spd'] * 60), 2) if s['spd'] > 0 else None for s in ds]  # min/km
    config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {"label": "HR (bpm)", "data": hr, "yAxisID": "yHR",
                 "borderColor": "rgb(220,50,50)", "pointRadius": 0, "borderWidth": 2, "fill": False},
                {"label": "Pace (min/km)", "data": pace, "yAxisID": "yPace",
                 "borderColor": "rgb(50,110,220)", "pointRadius": 0, "borderWidth": 2, "fill": False},
            ],
        },
        "options": {
            "scales": {
                "yHR": {"type": "linear", "position": "left"},
                "yPace": {"type": "linear", "position": "right", "reverse": True},
            },
            "plugins": {"title": {"display": True, "text": "Heart rate & pace over time (minutes)"}},
        },
    }
    encoded = urllib.parse.quote(json.dumps(config, separators=(',', ':')))
    return f"https://quickchart.io/chart?w=650&h=320&backgroundColor=white&c={encoded}"


def add_chart_and_series(
    notion_client: NotionClient,
    garmin_client: GarminClient,
    page_id: str,
    activity_id,
    activity_type: str,
    duration_min: float,
) -> tuple:
    # Fetch the second-by-second stream, write a chart image + a downsampled data toggle to the page,
    # and return (status, decoupling). status is "added", "skip" or "error".
    try:
        details = garmin_client.get_activity_details(activity_id, maxchart=2000, maxpoly=0)
    except Exception as e:
        print(f"  Could not fetch details for activity {activity_id}: {e}")
        return "error", None

    samples = _parse_detail_series(details)
    if len(samples) < 5:
        return "skip", None

    # Aerobic decoupling is only meaningful for steady runs of a reasonable length.
    decoupling = None
    if activity_type == "Running" and (duration_min or 0) >= 20:
        decoupling = _aerobic_decoupling(samples)

    # Chart image (best-effort: a bad or over-long URL must not cost us the analysis data below).
    header_blocks = [{"object": "block", "type": "heading_3",
                      "heading_3": {"rich_text": _rt("📈 In-workout charts & data")}}]
    chart_url = _build_chart_url(samples)
    if chart_url:
        header_blocks.append({"object": "block", "type": "image",
                              "image": {"type": "external", "external": {"url": chart_url}}})
    try:
        notion_client.blocks.children.append(block_id=page_id, children=header_blocks)
    except Exception as e:
        print(f"  Chart image skipped for page {page_id}: {e}")

    # Downsampled series (about 90 points), tucked in a collapsed toggle for precise analysis.
    step = max(1, len(samples) // 90)
    ds = samples[::step]
    series = {
        "t_s": [round(s['t']) for s in ds],
        "hr": [round(s['hr']) for s in ds],
        "spd_ms": [round(s['spd'], 2) for s in ds],
    }
    series_json = json.dumps(series, separators=(',', ':'))
    toggle_block = {
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": _rt("🔬 Downsampled time-series (for analysis)"),
            "children": [
                {"object": "block", "type": "code",
                 "code": {"language": "json", "rich_text": _rt_chunks(series_json)}}
            ],
        },
    }
    try:
        notion_client.blocks.children.append(block_id=page_id, children=[toggle_block])
    except Exception as e:
        print(f"  Could not write time-series to page {page_id}: {e}")
        return "error", decoupling
    return "added", decoupling


def create_activity(notion_client: NotionClient, database_id: str, activity: dict) -> dict:
    # Create a new activity in the Notion database
    activity_date = activity.get('startTimeGMT')
    activity_name = format_entertainment(activity.get('activityName', 'Unnamed Activity'))
    activity_type, activity_subtype = format_activity_type(
        activity.get('activityType', {}).get('typeKey', 'Unknown'),
        activity_name
    )

    # Get icon for the activity type
    icon_url = ACTIVITY_ICONS.get(activity_subtype if activity_subtype != activity_type else activity_type)

    properties = {
        "Date": {"date": {"start": activity_date}},
        "Activity Type": {"select": {"name": activity_type}},
        "Subactivity Type": {"select": {"name": activity_subtype}},
        "Activity Name": {"title": [{"text": {"content": activity_name}}]},
        "Distance (km)": {"number": round(activity.get('distance', 0) / 1000, 2)},
        "Duration (min)": {"number": round(activity.get('duration', 0) / 60, 2)},
        "Calories": {"number": round(activity.get('calories', 0))},
        "Avg HR": {"number": round(activity.get('averageHR') or 0)},
        "Max HR": {"number": round(activity.get('maxHR') or 0)},
        "Avg Cadence": {"number": round(activity.get('averageRunningCadenceInStepsPerMinute') or activity.get('averageBikingCadenceInRevPerMinute') or 0)},
        "Elevation Gain (m)": {"number": round(activity.get('elevationGain') or 0, 1)},
        "Training Load": {"number": round(activity.get('activityTrainingLoad') or 0, 1)},
        "Avg Pace": {"rich_text": [{"text": {"content": format_pace(activity.get('averageSpeed', 0))}}]},
        "Avg Power": {"number": round(activity.get('avgPower', 0), 1)},
        "Max Power": {"number": round(activity.get('maxPower', 0), 1)},
        "Training Effect": {"select": {"name": format_training_effect(activity.get('trainingEffectLabel', 'Unknown'))}},
        "Aerobic": {"number": round(activity.get('aerobicTrainingEffect', 0), 1)},
        "Aerobic Effect": {
            "select": {"name": format_training_message(activity.get('aerobicTrainingEffectMessage', 'Unknown'))}
        },
        "Anaerobic": {"number": round(activity.get('anaerobicTrainingEffect', 0), 1)},
        "Anaerobic Effect": {
            "select": {"name": format_training_message(activity.get('anaerobicTrainingEffectMessage', 'Unknown'))}
        },
        "PR": {"checkbox": activity.get('pr', False)},
        "Fav": {"checkbox": activity.get('favorite', False)}
    }

    page = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }

    if icon_url:
        page["icon"] = {"type": "external", "external": {"url": icon_url}}

    return notion_client.pages.create(**page)


def update_activity(notion_client: NotionClient, existing_activity: dict, new_activity: dict) -> None:
    # Update an existing activity in the Notion database with new data
    activity_name = new_activity.get('activityName', 'Unnamed Activity')
    activity_type, activity_subtype = format_activity_type(
        new_activity.get('activityType', {}).get('typeKey', 'Unknown'),
        activity_name
    )

    # Get icon for the activity type
    icon_url = ACTIVITY_ICONS.get(activity_subtype if activity_subtype != activity_type else activity_type)

    properties = {
        "Activity Type": {"select": {"name": activity_type}},
        "Subactivity Type": {"select": {"name": activity_subtype}},
        "Distance (km)": {"number": round(new_activity.get('distance', 0) / 1000, 2)},
        "Duration (min)": {"number": round(new_activity.get('duration', 0) / 60, 2)},
        "Calories": {"number": round(new_activity.get('calories', 0))},
        "Avg HR": {"number": round(new_activity.get('averageHR') or 0)},
        "Max HR": {"number": round(new_activity.get('maxHR') or 0)},
        "Avg Cadence": {"number": round(new_activity.get('averageRunningCadenceInStepsPerMinute') or new_activity.get('averageBikingCadenceInRevPerMinute') or 0)},
        "Elevation Gain (m)": {"number": round(new_activity.get('elevationGain') or 0, 1)},
        "Training Load": {"number": round(new_activity.get('activityTrainingLoad') or 0, 1)},
        "Avg Pace": {"rich_text": [{"text": {"content": format_pace(new_activity.get('averageSpeed', 0))}}]},
        "Avg Power": {"number": round(new_activity.get('avgPower', 0), 1)},
        "Max Power": {"number": round(new_activity.get('maxPower', 0), 1)},
        "Training Effect": {
            "select": {"name": format_training_effect(new_activity.get('trainingEffectLabel', 'Unknown'))}
        },
        "Aerobic": {"number": round(new_activity.get('aerobicTrainingEffect', 0), 1)},
        "Aerobic Effect": {
            "select": {"name": format_training_message(new_activity.get('aerobicTrainingEffectMessage', 'Unknown'))}
        },
        "Anaerobic": {"number": round(new_activity.get('anaerobicTrainingEffect', 0), 1)},
        "Anaerobic Effect": {
            "select": {"name": format_training_message(new_activity.get('anaerobicTrainingEffectMessage', 'Unknown'))}
        },
        "PR": {"checkbox": new_activity.get('pr', False)},
        "Fav": {"checkbox": new_activity.get('favorite', False)}
    }

    update = {
        "page_id": existing_activity['id'],
        "properties": properties,
    }

    if icon_url:
        update["icon"] = {"type": "external", "external": {"url": icon_url}}

    notion_client.pages.update(**update)


def main():
    load_dotenv()

    # Initialize Garmin and Notion clients using environment variables
    garmin_client, garmin_configuration = get_garmin_client()
    notion_client, notion_dbs = get_notion_client()

    database_id = notion_dbs.activities

    # Get all activities
    activities = get_all_activities(garmin_client, 70)

    # Process all activities
    for activity in activities:
        activity_date_raw: str = activity.get('startTimeGMT')
        activity_date: datetime = (
            datetime
            .strptime(activity_date_raw, '%Y-%m-%d %H:%M:%S')  # Parse as format received from Garmin
            .replace(tzinfo=UTC)  # Set timezone to UTC, as Garmin times are in GMT/UTC. Close enough.
        )

        activity_name = format_entertainment(activity.get('activityName', 'Unnamed Activity'))
        activity_type, activity_subtype = format_activity_type(
            activity.get('activityType', {}).get('typeKey', 'Unknown'),
            activity_name
        )

        # Check if activity already exists in Notion
        existing_activity = activity_exists(notion_client, database_id, activity_date, activity_type, activity_name)

        if existing_activity:
            if activity_needs_update(existing_activity, activity):
                update_activity(notion_client, existing_activity, activity)
                # print(f"Updated: {activity_type} - {activity_name}")
            page_id = existing_activity['id']
            has_laps = (existing_activity['properties'].get('Has Lap Data') or {}).get('checkbox') or False
        else:
            created_page = create_activity(notion_client, database_id, activity)
            page_id = created_page.get('id') if created_page else None
            has_laps = False

        # Phase 2: write the per-lap interval breakdown onto the page (once per activity)
        activity_id = activity.get('activityId')
        if page_id and activity_id and not has_laps:
            lap_status = add_lap_data(notion_client, garmin_client, page_id, activity_id)
            if lap_status in ("added", "skip"):
                notion_client.pages.update(
                    page_id=page_id,
                    properties={"Has Lap Data": {"checkbox": True}},
                )
            time.sleep(0.3)  # be gentle on the Garmin API

        # Phase 2: charts + downsampled series + decoupling metric (once per activity)
        has_charts = False
        if existing_activity:
            has_charts = (existing_activity['properties'].get('Has Charts') or {}).get('checkbox') or False
        if page_id and activity_id and not has_charts:
            duration_min = round(activity.get('duration', 0) / 60, 2)
            chart_status, decoupling = add_chart_and_series(
                notion_client, garmin_client, page_id, activity_id, activity_type, duration_min
            )
            if chart_status in ("added", "skip"):
                chart_props = {"Has Charts": {"checkbox": True}}
                if decoupling is not None:
                    chart_props["Aerobic Decoupling (%)"] = {"number": decoupling}
                notion_client.pages.update(page_id=page_id, properties=chart_props)
            time.sleep(0.4)  # be gentle on the Garmin API


if __name__ == '__main__':
    main()
