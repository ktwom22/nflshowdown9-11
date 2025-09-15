from flask import Flask, render_template_string, request
import pandas as pd
import requests
from io import StringIO
from itertools import combinations

app = Flask(__name__)

GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSwiZjQ1dK9Wxe_GYcoeELm3-nXy-xGEG4WSbCXuk-JClxcF9kEseWhAovCsWwx_8NkgoSryDNKZATO/pub?gid=1256404350&single=true&output=csv"
SALARY_CAP = 50000

def clean_data(df, selected_date=None):
    df = df.dropna(how="all")
    df.columns = df.columns.str.strip().str.upper()

    # rename relevant columns
    rename_map = {}
    for col in df.columns:
        if "PLAYER" in col:
            rename_map[col] = "Name"
        elif "SALARY" in col:
            rename_map[col] = "Salary"
        elif "FINAL POINTS" in col or (col == "FINAL POINTS1"):
            rename_map[col] = "Proj"
        elif col == "TEAM" or col == "TEAM":
            rename_map[col] = "Team"
        elif col == "POS":
            rename_map[col] = "POS"
        elif "DATE" in col:
            rename_map[col] = "Date"
    df = df.rename(columns=rename_map)

    # Check required columns
    required = ["Name", "Salary", "Proj", "POS"]
    for r in required:
        if r not in df.columns:
            raise ValueError(f"Column '{r}' not found in sheet after renaming. Available columns: {list(df.columns)}")

    # Clean date if exists
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
        if selected_date:
            try:
                parsed = pd.to_datetime(selected_date).date()
                df = df[df["Date"] == parsed]
            except Exception as e:
                # If parsing fails, ignore filter
                print("Warning: could not parse selected_date:", selected_date, "error:", e)
    else:
        # If no date column, selected_date should be ignored
        pass

    # Clean salary
    df["Salary"] = df["Salary"].astype(str).str.replace(r'[\$,]', '', regex=True)
    # Remove rows where Salary is not numeric
    mask_sal = df["Salary"].str.replace('.', '', 1).str.isnumeric()
    df = df[mask_sal]
    df["Salary"] = df["Salary"].astype(float)

    # Clean projection
    df["Proj"] = pd.to_numeric(df["Proj"], errors="coerce")

    # Clean rest
    df["Name"] = df["Name"].astype(str).str.strip()
    df["Team"] = df["Team"].astype(str).str.strip().str.upper()
    df["POS"] = df["POS"].astype(str).str.strip().str.upper()

    df = df.dropna(subset=["Name","Salary","Proj","POS"])
    df = df.drop_duplicates(subset=["Name"]).reset_index(drop=True)
    return df[["Name","Team","POS","Salary","Proj"]]

def generate_all_lineups(df, lock_cpt=None, lock_flex=[], exclude=[], max_lineups=5):
    players = df.copy()
    if exclude:
        players = players[~players["Name"].isin(exclude)]
    if len(players) < 6:
        return []

    cpt_candidates = players[players["Name"] == lock_cpt] if lock_cpt else players
    all_lineups = []

    for flex in combinations(players["Name"], 5):
        if lock_flex:
            if not all(f in flex for f in lock_flex):
                continue
        for cpt_row in cpt_candidates.itertuples():
            if cpt_row.Name in flex:
                continue
            lineup = []
            lineup.append({
                "Name": cpt_row.Name,
                "Role": "CPT",
                "Salary": int(round(cpt_row.Salary * 1.5, -2)),
                "Proj": cpt_row.Proj * 1.5,
                "Team": cpt_row.Team
            })
            for f in flex:
                f_row = players[players["Name"] == f].iloc[0]
                lineup.append({
                    "Name": f_row.Name,
                    "Role": "FLEX",
                    "Salary": f_row.Salary,
                    "Proj": f_row.Proj,
                    "Team": f_row.Team
                })
            total_salary = sum(p["Salary"] for p in lineup)
            if total_salary <= SALARY_CAP:
                total_proj = sum(p["Proj"] for p in lineup)
                all_lineups.append({"players": lineup, "Salary": total_salary, "Projected": total_proj})
            if len(all_lineups) >= max_lineups:
                break
        if len(all_lineups) >= max_lineups:
            break

    all_lineups = sorted(all_lineups, key=lambda x: x["Projected"], reverse=True)
    return all_lineups[:max_lineups]

PLAYER_HTML_TEMPLATE = """
<!DOCTYPE html><html><head><title>Player Pool</title></head><body>
<h1>NFL Showdown Player Pool</h1>
<form method="get" action="/">
<label>Select Slate Date:</label>
<select name="date" onchange="this.form.submit()">
{% for d in dates %}
  <option value="{{ d }}" {% if d|string == selected_date|string %} selected {% endif %}>{{ d }}</option>
{% endfor %}
</select>
</form>
<p>Total Players: {{ players|length }}</p>
<form method="get" action="/lineups">
  <input type="hidden" name="date" value="{{ selected_date }}">
  <label>Number of Lineups:</label>
  <input type="number" name="count" value="1" min="1" max="10">
  <br><br>
  <table border="1" cellpadding="5">
    <tr><th>Name</th><th>Team</th><th>POS</th><th>Salary</th><th>Proj</th><th>CPT Lock</th><th>FLEX Lock</th><th>Exclude</th></tr>
    {% for p in players %}
    <tr>
      <td>{{ p.Name }}</td><td>{{ p.Team }}</td><td>{{ p.POS }}</td><td>${{ "{:,.0f}".format(p.Salary) }}</td><td>{{ "%.2f"|format(p.Proj) }}</td>
      <td><input type="radio" name="lock_cpt" value="{{ p.Name }}"></td>
      <td><input type="checkbox" name="lock_flex" value="{{ p.Name }}"></td>
      <td><input type="checkbox" name="exclude" value="{{ p.Name }}"></td>
    </tr>
    {% endfor %}
  </table><br>
  <button type="submit">Generate Lineups</button>
</form>
</body></html>
"""

LINEUP_HTML_TEMPLATE = """
<!DOCTYPE html><html><head><title>Lineups</title></head><body>
<h1>Generated Lineups</h1>
{% if error %}
  <p style="color:red;">{{ error }}</p>
{% endif %}
{% for lu in lineups %}
  <h3>Lineup {{ loop.index }} — Salary: ${{ "{:,.0f}".format(lu.Salary) }} — Proj: {{ "%.2f"|format(lu.Projected) }}</h3>
  <table border="1" cellpadding="5">
    <tr><th>Role</th><th>Name</th><th>Team</th><th>Salary</th><th>Proj</th></tr>
    {% for p in lu.players %}
    <tr>
      <td>{{ p.Role }}</td><td>{{ p.Name }}</td><td>{{ p.Team }}</td><td>${{ "{:,.0f}".format(p.Salary) }}</td><td>{{ "%.2f"|format(p.Proj) }}</td>
    </tr>
    {% endfor %}
  </table><br>
{% endfor %}
<form method="get" action="/"><button>⬅ Back</button></form>
</body></html>
"""

@app.route('/')
def player_pool():
    selected_date = request.args.get("date")
    try:
        r = requests.get(GOOGLE_SHEET_CSV_URL, timeout=10)
        r.raise_for_status()
        df_full = pd.read_csv(StringIO(r.text))
        df_full.columns = df_full.columns.str.strip().str.upper()

        # ensure there is a date column
        date_cols = [c for c in df_full.columns if "DATE" in c]
        if not date_cols:
            return "<p>❌ Error: sheet must include a 'Date' column, but none found. Columns: {}.</p>".format(", ".join(df_full.columns))
        # pick first date-like column
        date_col = date_cols[0]
        # parse
        df_full[date_col] = pd.to_datetime(df_full[date_col], errors="coerce").dt.date

        # collect all dates
        # dropna so only real dates
        dates_series = df_full[date_col].dropna()
        # make sure it's a Series of scalar types
        # unique returns numpy array
        unique_dates = dates_series.unique()
        # convert to list of strings
        all_dates = sorted([str(d) for d in unique_dates], reverse=True)

        if not selected_date and all_dates:
            selected_date = all_dates[0]

        # filter and clean
        df = clean_data(df_full, selected_date)
        players = df.to_dict(orient="records")

        return render_template_string(PLAYER_HTML_TEMPLATE, players=players, dates=all_dates, selected_date=selected_date)
    except Exception as e:
        return f"<p>Error loading player pool: {e}</p>"

@app.route('/lineups')
def generate_lineups():
    selected_date = request.args.get("date")
    try:
        r = requests.get(GOOGLE_SHEET_CSV_URL, timeout=10)
        r.raise_for_status()
        df_full = pd.read_csv(StringIO(r.text))
        df_full.columns = df_full.columns.str.strip().str.upper()

        df = clean_data(df_full, selected_date)

        lock_cpt = request.args.get("lock_cpt")
        lock_flex = request.args.getlist("lock_flex")
        exclude = request.args.getlist("exclude")
        count = int(request.args.get("count", 1))
        count = max(1, min(count, 10))

        lineups = generate_all_lineups(df, lock_cpt, lock_flex, exclude, max_lineups=count)
        if not lineups:
            return render_template_string(LINEUP_HTML_TEMPLATE, lineups=[], error="Could not generate any valid lineups.")
        return render_template_string(LINEUP_HTML_TEMPLATE, lineups=lineups, error=None)
    except Exception as e:
        return f"<p>Error generating lineup: {e}</p>"

if __name__ == '__main__':
    app.run(debug=True)
