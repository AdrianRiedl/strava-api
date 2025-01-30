import base64
import collections
import datetime
import io
import json
import math
import sys
from tqdm import tqdm
import requests
import pandas as pd
import folium
import polyline
import os
import time
import matplotlib.pyplot as plt
from folium.plugins import HeatMap
from tabulate import tabulate
import argparse
from src.api_methods import authorize


# define function to return NaN as 0
def makeNaNZero(a):
    return a if math.isnan(a) else 0


# get your strava data
def get_data(access_token, per_page=200, page=1):
    url = 'https://www.strava.com/api/v3/athlete/activities'
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {'per_page': per_page, 'page': page}
    return requests.get(url, headers=headers, params=params).json()


# get the gear with specific id
def get_gear(access_token, id):
    url = f'https://www.strava.com/api/v3/gear/{id}'
    headers = {'Authorization': f'Bearer {access_token}'}
    return requests.get(url, headers=headers).json()


# get the activity with specific id
def get_activity(access_token, id):
    url = f'https://www.strava.com/api/v3/activities/{id}'
    headers = {'Authorization': f'Bearer {access_token}'}
    return requests.get(url, headers=headers).json()


access_token: str = authorize.get_acces_token()


# download the data from the strava website
def downloadStravaData():
    print("Downloading from Strava")

    data = list()
    page_number = 1
    while True:
        page_data = get_data(access_token, page=page_number)
        page_number += 1
        if not page_data:
            break
        data.append(page_data)

    data_dictionaries = []
    for page in data:
        data_dictionaries.extend(page)
    # print number of activities
    print('Number of activities downloaded: {}'.format(len(data_dictionaries)))
    return data_dictionaries


# resolve the points to their elevation above sea level
def get_elevation(vec):
    payload = {'locations': [{"latitude": lat, "longitude": lon} for lat, lon in vec]}
    r = requests.post(url="https://api.open-elevation.com/api/v1/lookup",
                      headers={
                          "Accept": "application/json",
                          "Content-Type": "application/json; charset=utf-8",
                      },
                      data=json.dumps(payload)).json()
    return [entry['elevation'] for entry in r.get('results', [])]


def runPreprocessing(activities):
    # convert data types
    activities.loc[:, 'start_date'] = pd.to_datetime(activities['start_date']).dt.tz_localize(None)
    activities.loc[:, 'start_date_local'] = pd.to_datetime(activities['start_date_local']).dt.tz_localize(None)
    # convert values
    activities.loc[:, 'distance'] /= 1000  # convert from m to km
    activities.loc[:, 'average_speed'] *= 3.6  # convert from m/s to km/h
    activities.loc[:, 'max_speed'] *= 3.6  # convert from m/s to km/h
    # set index
    activities.set_index('start_date_local', inplace=True)
    # drop columns
    activities.drop(
        [
            'resource_state',
            'external_id',
            'upload_id',
            'location_city',
            'location_state',
            'has_kudoed',
            'start_date',
            'athlete.resource_state',
            'utc_offset',
            'map.resource_state',
            'athlete.id',
            'visibility',
            'heartrate_opt_out',
            'upload_id_str',
            'from_accepted_tag',
            'map.id',
            'manual',
            'private',
            'flagged',
        ],
        axis=1,
        inplace=True
    )
    return activities


def getData(refreshDownload):
    if not os.path.isfile('activities.csv') or refreshDownload:
        data_dictionaries = downloadStravaData()
        # normalize data
        activities = pd.json_normalize(data_dictionaries)
        # store it as a csv file
        activities.to_csv("activities.csv")
    else:
        activities = pd.read_csv('activities.csv')
    return activities


def filterActivities(activities, sinceDate, untilDate, activityTypes):
    # update the until timestamp to the last possible one of the day
    untilDate = untilDate if untilDate is None else untilDate.replace(hour=23, minute=59, second=59)
    # build the filter for the activities
    activityTypeFilter = '' if activityTypes is None else ' | '.join([f'sport_type == \'{a}\'' for a in activityTypes])
    # build the filter for the date
    sinceDataFilter = '' if sinceDate is None else f'(start_date >= \"{sinceDate}\")'
    untilDataFilter = '' if untilDate is None else f'(start_date <= \"{untilDate}\")'
    # join the filter
    f = ' & '.join(filter(None, [activityTypeFilter, sinceDataFilter, untilDataFilter]))
    print(f"The filter is \"{f}\".")
    # apply the filter
    if not f:
        return activities
    return activities.query(f)


# color scheme
settings = {
    'Ride': {'color': 'red', 'icon': 'bicycle', 'process': True,
             'subcategories': {'Ride': 0, 'GravelRide': 10, 'MountainBikeRide': 20}},
    'VirtualRide': {'color': 'red', 'icon': 'bicycle', 'process': True, 'subcategories': {'Ride': 0}},
    'Run': {'color': 'green', 'icon': 'person', 'process': True, 'subcategories': {'Run': 0}},
    'Hike': {'color': 'purple', 'icon': 'person', 'process': True, 'subcategories': {'Hike': 0}},
    'Walk': {'color': 'purple', 'icon': 'person', 'process': True, 'subcategories': {'Walk': 0}},
    'Swim': {'color': 'blue', 'icon': 'water', 'process': True, 'subcategories': {'Swim': 0}},
    'NordicSki': {'color': 'lightblue', 'icon': 'ski', 'process': True, 'subcategories': {'NordicSki': 0}}}

# get the available subcategories
activityTypes = [subcat for details in settings.values() for subcat in details.get('subcategories', {}).keys()]


def main(args):
    activities = getData(args.refresh)
    activities = filterActivities(activities, args.since, args.until, args.type)

    m = folium.Map(location=(48.1372, 11.5755), zoom_start=4)
    # # add full screen button
    folium.plugins.Fullscreen().add_to(m)

    sports = {}
    markersGroup = folium.FeatureGroup(name='Show markers')
    markersGroup.add_to(m)
    for c in settings.keys():
        sports[c] = folium.FeatureGroup(name=c)
        sports[c].add_to(m)

    # create dictionary with elevation profiles
    elevation_profile = dict()

    # do some preprocessing
    activities = activities.dropna(subset=['map.summary_polyline'])
    activities = runPreprocessing(activities)

    # map of gear to year to month to distance and elevationl
    gearDistanceElevationMap = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: (0.0, 0.0))))
    gearMap = {}

    for row in tqdm(activities.iterrows(), desc="Plotting progress", total=activities.shape[0]):
        row_index = row[0]
        row_values = row[1]
        type = row_values['type']

        year = row[0].year
        month = row[0].month

        # query the gear if present and not yet known
        if isinstance(row_values['gear_id'], str):
            gear = row_values['gear_id']
            gearDistanceElevationMap[gear][year][month] = (
                gearDistanceElevationMap[gear][year][month][0] + float(row_values['distance']),
                gearDistanceElevationMap[gear][year][month][1] + float(row_values['total_elevation_gain']))
            if gear not in gearMap:
                gearMap[gear] = get_gear(access_token, gear)

        # option to skip specific activity types
        if type not in settings or not settings[type]['process']:
            print(f"\n{row_values['id']} {row_values['name']} {type}: skipping as not process set")
            continue
        # decode the polyline
        line = polyline.decode(row_values['map.summary_polyline'])
        # if the decided line is empty, skip this activity
        if not line:
            print(f"\n{row_values['id']} {row_values['name']} {type}: skipping as it is empty")
            continue
        # get the elevation
        # retry for the elevation until success or at most 10 times
        elevation = []
        retry = True
        counter = 0
        while retry and counter < 10:
            try:
                elevation = get_elevation(line)
                retry = False
            except:
                print(f"Retrying elevation for {row_values['id']}")
                time.sleep(5)
                counter = counter + 1

        # halfway_coord = line[0]  # line[int(len(line) / 2)]
        halfway_coord = line[int(len(line) / 2)]

        pictureText = 'iVBORw0KGgoAAAANSUhEUgAAAHAAAAA4CAYAAAAl63xKAAAABHNCSVQICAgIfAhkiAAAABl0RVh0U29mdHdhcmUAZ25vbWUtc2NyZWVuc2hvdO8Dvz4AAAAtdEVYdENyZWF0aW9uIFRpbWUARnJpIDA4IE1hciAyMDI0IDEwOjQ4OjU4IEFNIENFVHmib7gAAACdSURBVHic7dHBCQAgEMAwdf+dzyF8SCGZoNA9M7PIOr8DeGNgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxF0PVBGzyjItLAAAAAElFTkSuQmCC'

        if len(elevation) > 0:
            # plot elevation profile
            fig, ax = plt.subplots(figsize=(10, 4))
            ax = pd.Series(elevation).rolling(3).mean().plot(
                ax=ax,
                color='steelblue',
                legend=False
            )
            ax.set_ylabel('Elevation')
            ax.axes.xaxis.set_visible(False)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)

            # make the plot and convert it to base64
            pic_IObytes = io.BytesIO()
            plt.savefig(pic_IObytes, format='png', dpi=75)
            plt.close()
            pic_IObytes.seek(0)
            pictureText = base64.b64encode(pic_IObytes.read()).decode()

        elevation_profile[row_values['id']] = pictureText

        # popup text
        html = """
        <h3>{}</h3>
            <p style="font-family:'Courier New'" font-size=30px>
                Date : {} <br>
                Time : {} <br>
                <a href="https://www.strava.com/activities/{}" target="_blank">Activity</a>
            </p>
        <h4>{}</h4>
            <p style="font-family:'Courier New'" font-size=30px>
                Distance&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.2f} km <br>
                Elevation Gain&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.0f} m <br>
                Moving Time&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {} <br>
                Average Speed&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.2f} km/h (maximum: {:.2f} km/h) <br>
                Average Watts&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.1f} W (maximum: {:.1f} W) <br>
            </p>
            <img src="data:image/png;base64,{}">
        """.format(
            row_values['name'],
            row_index.date(),
            row_index.time(),
            row_values['id'],
            type,
            row_values['distance'],
            row_values['total_elevation_gain'],
            time.strftime('%H:%M:%S', time.gmtime(row_values['moving_time'])),
            row_values['average_speed'], row_values['max_speed'],
            makeNaNZero(row_values.get('average_watts', 0)), makeNaNZero(row_values.get('max_watts', 0)),
            elevation_profile[row_values['id']],
        )

        # add marker to map
        icon = folium.Icon(color=settings[type]['color'],
                           icon=settings[type]['icon'], icon_color="white", prefix='fa')

        # plot the activity
        if row_values['sport_type'] not in settings[type]['subcategories']:
            settings[type]['subcategories'][row_values['sport_type']] = 0
        l = folium.PolyLine(line, color=settings[type]['color'], popup=html,
                            dash_array=settings[type]['subcategories'][row_values['sport_type']])
        sports[type].add_child(l)

        marker = folium.Marker(location=halfway_coord, icon=icon)
        markersGroup.add_child(marker)
        time.sleep(0.2)

    # Add dark and light mode.
    # folium.TileLayer('cartodbdark_matter', name="dark mode", control=True).add_to(m)
    # folium.TileLayer('cartodbpositron', name="light mode", control=True).add_to(m)

    # We add a layer controller.
    folium.LayerControl(collapsed=False).add_to(m)

    formatDate = lambda date: '' if date is None else date.strftime('%Y-%m-%d')
    if not args.noPlot:
        m.save(f'route{formatDate(args.since)}{formatDate(args.until)}.html')
    print(settings)
    print(gearDistanceElevationMap)
    print(gearMap)

    text = ""

    gearToTable = {}

    def mapToMonth(month):
        assert 1 <= month <= 12
        month_map = {
            1: "January",
            2: "February",
            3: "March",
            4: "April",
            5: "May",
            6: "June",
            7: "July",
            8: "August",
            9: "September",
            10: "October",
            11: "November",
            12: "December"
        }
        return month_map[month]

    for gear, years in gearDistanceElevationMap.items():
        if gear not in gearToTable:
            gearToTable[gear] = []
        for year, months in years.items():
            for month, (dist, elev) in months.items():
                gearToTable[gear].append([year, mapToMonth(month), dist, elev])

    print(gearToTable)

    for gear, l in gearToTable.items():
        text += "------------------------------------------------------------------------\n"
        text += f"For {gearMap[gear]["nickname"]}:\n"
        yearMap = collections.defaultdict(lambda: (0.0, 0.0))
        for e in l:
            year = e[0]
            distance = e[2]
            elevation = e[3]
            yearMap[year] = (yearMap[year][0] + distance, yearMap[year][1] + elevation)
        subTable = []
        for year, (d, e) in yearMap.items():
            subTable.append([year, d, e])
        text += tabulate(subTable, headers=['Year', 'Distance', 'Elevation'], tablefmt='github') + "\n"
        text += "-------------------------------------------------\n"
        text += tabulate(l, headers=['Year', 'Month', 'Distance', 'Elevation'], tablefmt='github') + "\n"

    print(text)
    with open("into.txt", 'w') as f:
        f.write(text)


def printHelp():
    print("Usage: python3 main.py [--refresh]")
    print("It downloads the data from Strava and visualizes it.")
    print("It only downloads the data if no 'activity.csv' file exists or if the flag '--refresh' is set.")


if __name__ == '__main__':
    # Instantiate the parser
    parser = argparse.ArgumentParser(
        prog='plot.py',
        description='Plot the routes from Strava')

    parser.add_argument('-s', '--since', metavar='YYYY-mm-dd',
                        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'))  # option that takes a value
    parser.add_argument('-u', '--until', metavar='YYYY-mm-dd',
                        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'))  # option that takes a value
    parser.add_argument('-t', '--type', choices=activityTypes, action='append')  # option that takes a value
    parser.add_argument('-r', '--refresh', action='store_true')  # on/off flag
    # parser.add_argument('-e', '--exact', action='store_true')  # on/off flag
    parser.add_argument('--noPlot', action='store_true')  # on/off flag
    args = parser.parse_args()
    print(args.since, args.type, args.refresh)

    main(args)
