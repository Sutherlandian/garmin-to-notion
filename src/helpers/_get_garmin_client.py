import os
from dataclasses import dataclass

from dotenv import load_dotenv
from garminconnect import Garmin
@dataclass(frozen=True)
class GarminConfiguration:
activity_fetch_limit: int
def get_garmin_client() -> tuple[Garmin, GarminConfiguration]:
load_dotenv()
print("Initializing Garmin client...")
garmin_client = _get_garmin_client()
garmin_configuration = _get_garmin_configuration()
print("Garmin client authenticated successfully.")
return garmin_client, garmin_configuration
def _get_garmin_client() -> Garmin:
garmin_email = os.getenv("GARMIN_EMAIL")
garmin_password = os.getenv("GARMIN_PASSWORD")
garmin_tokenstore = os.getenv("GARMINTOKENS", ".garminconnect")
if not garmin_email:
raise ValueError(
"GARMIN_EMAIL is required. "
"Add it as a GitHub Actions repository secret."
)
if not garmin_password:
raise ValueError(
"GARMIN_PASSWORD is required. "
"Add it as a GitHub Actions repository secret."
)
garmin_client = Garmin(
email=garmin_email,
password=garmin_password,
)
garmin_client.login(garmin_tokenstore)
return garmin_client
def _get_garmin_configuration() -> GarminConfiguration:
return GarminConfiguration(
activity_fetch_limit=int(
os.getenv("GARMIN_ACTIVITIES_FETCH_LIMIT", "10")
),
)
