 # Aviation Delays & Weather Modeling

**Status:** M1 - ingestion complete

## The Question
This project aims to predict flight delay probabilities and duration based on meteorological conditions at both the departure and arrival locations. By joining flight logs with localized weather data, we can explain how specific storm or wind profiles impact aviation logistics. Ultimately, this model will identify which specific flight routes are most vulnerable to cascading delays during adverse weather events.

## Data Sources
* [OpenSky Network API](docs/sources.md) - Flight tracking data (Primary Source). *Attribution: Data provided by the OpenSky Network (https://opensky-network.org).*
* [Open-Meteo API](docs/sources.md) - Hourly localized weather data (Secondary Source). *Attribution: Weather data provided by Open-Meteo (https://open-meteo.com).*

## How to Run
Follow these steps to complete a full data ingestion run from a fresh clone:

```bash
# 1. Clone the repository
git clone [https://github.com/se-230717030/Aviation_Delays_Weather_Modeling](https://github.com/se-230717030/Aviation_Delays_Weather_Modeling)
cd Aviation_Delays_Weather_Modeling

# 2. Set up the environment
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Open the .env file in your text editor and update the following values:
# OPENSKY_USERNAME=your_registered_username
# OPENSKY_PASSWORD=your_registered_password

# 5. Run the ingestion script
python src/ingest.py

```
## Repository Structure
```text
aviation-delays-weather/
├── README.md             # Project overview and instructions
├── .gitignore            # Files and folders to be ignored by Git (e.g., .env, data/raw/)
├── .env.example          # Template for environment variables required by the script
├── requirements.txt      # Python dependencies required to run the script
├── data/
│   └── raw/              # Target directory for unmodified, downloaded JSON/CSV data
├── src/
│   └── ingest.py         # Main Python script to fetch and save data from the APIs
└── docs/
    └── sources.md        # Data source cards detailing provenance, licensing, and terms
```


    

## Team
* Muhammet Ali Öztürk (230717030) - GitHub: @se-230717030
* Muhammed Osman Kara (230717031) - GitHub: @se-230717031
* Oğuzhan Şükrü Keleş (230717016) - GitHub: @se-230717016