# Wind Forecast Desk

A web app that helps an energy team review day-ahead wind forecasts using Elexon data.

**Live App:** https://kjhwr-nine.vercel.app/

## Features

- Shows the latest wind forecast available at the selected decision time.
- Compares forecasts with actual wind generation.
- Displays forecast errors by day, hour, and forecast lead time.
- Shows accuracy measures such as MAE, RMSE, bias, and weighted MAPE.
- Shows a likely error range based on the previous 14 completed delivery days.

## Data Sources

This project uses Elexon data:

- **WINDFOR:** Wind power forecasts.
- **FUELHH:** Actual wind generation recorded every 30 minutes.

The app compares each hourly forecast with the average of the two half-hour actual readings. If either reading is missing, that hour is excluded.

For each delivery hour, the app selects the latest forecast published at or before the selected decision time. The default cut-off is **09:00 UTC**. This is an assumption for the project and should be checked before real trading use.

## Accuracy Results

For the January 2026 data, using the default 09:00 UTC cut-off:

| Measure | Result |
|---|---:|
| MAE | 2,039 MW |
| RMSE | 2,505 MW |
| Average bias | +1,748 MW |
| Weighted MAPE | 18.1% |

A positive bias means the forecast was higher than the FUELHH actual on average.

FUELHH does not cover every wind source in Great Britain. An extra comparison with Elexon's AGWS data suggests that differences in data coverage explain much of the measured gap, but the exact reason has not been confirmed. The dashboard uses FUELHH for its main accuracy results.

## Tech Stack

- **Frontend:** React, Vite, Recharts
- **Backend:** Python, FastAPI
- **Data:** Elexon WINDFOR and FUELHH
- **Deployment:** Vercel
- **Testing:** Pytest

## Run Locally

You need Python and Node.js installed.

**1. Start the backend**

From the project root, run:

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**2. Start the frontend**

Open a second terminal from the project root:

```bash
cd frontend
npm install
npm run dev
```

Open the local URL shown by Vite, usually `http://localhost:5173`.

## Run with Docker

If Docker Desktop is installed and running, run this from the project root:

```bash
docker compose up --build
```

Then open `http://localhost:8000`.

## Run Tests

From the project root:

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

The project test suite contains 97 tests covering forecast selection, data matching, accuracy calculations, and other key checks.

## Limitations

- The default 09:00 UTC decision cut-off is an assumption.
- Differences in data coverage may affect the reported forecast bias.
- The app focuses on national wind forecasts. It does not include demand or electricity prices.
- The likely error range applies to each hour, not the full day's total.

## AI Assistance

AI tools were used to help with code development, debugging, and project setup.

## Data Links

- [Elexon Generation by Fuel Type](https://bmrs.elexon.co.uk/generation-by-fuel-type)
- [Elexon BMRS](https://bmrs.elexon.co.uk/)
