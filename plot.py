import base64
import collections
import datetime
import io
import json
import math
import sys
import matplotlib
matplotlib.use('Agg')

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
import gpxpy
import haversine as hs
from routeInfo import RouteInfo, settings

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
    max_number_of_pages = 10
    data = list()
    for page_number in tqdm(range(1, max_number_of_pages + 1)):
        page_data = get_data(access_token, page=page_number)
        if not page_data:
            break
        data.append(page_data)

        # data dictionaries
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


# get the available subcategories
activityTypes = [subcat for details in settings.values() for subcat in details.get('subcategories', {}).keys()]


def genStravaWithClass(activities, markersGroup, sports, gearDistanceElevationMap, gearMap):
    for row in tqdm(activities.iterrows(), desc="Plotting progress [Strava]:", total=activities.shape[0]):
        row_values = row[1]

        # decode the polyline
        line = polyline.decode(row_values['map.summary_polyline'])

        tour_info = RouteInfo(
            row_values['name'],
            row_values['id'],
            'Strava',
            row_values['type'],
            row_values['sport_type'],
            row[0],
            line,
            row_values['distance'],
            row_values['total_elevation_gain'],
            datetime.timedelta(seconds=row_values['moving_time']),
            row_values['average_speed'],
            row_values['max_speed'],
            row_values.get('average_watts', 0),
            row_values.get('max_watts', 0),
        )

        # query the gear if present and not yet known
        if isinstance(row_values['gear_id'], str):
            gear = row_values['gear_id']
            gearDistanceElevationMap[gear][tour_info.get_year()][tour_info.get_month()] = (
                gearDistanceElevationMap[gear][tour_info.get_year()][tour_info.get_month()][0] + float(row_values['distance']),
                gearDistanceElevationMap[gear][tour_info.get_year()][tour_info.get_month()][1] + float(row_values['total_elevation_gain']))
            if gear not in gearMap:
                gearMap[gear] = get_gear(access_token, gear)

        if not tour_info.process_tour():
            print(f'\n{tour_info.get_debug_description()}: skipping as set as "not process"')
            continue
        if not tour_info.line:
            print(f'\n{tour_info.get_debug_description()}: skipping as no .gpx file found"')
            continue

        # get the elevation
        # retry for the elevation until success or at most 10 times
        elevation = []
        retry = True
        counter = 0
        while retry and counter < 10:
            try:
                elevation = get_elevation(tour_info.line)
                retry = False
            except:
                print(f"Retrying elevation for {row_values['id']}")
                time.sleep(5)
                counter = counter + 1

        if len(elevation) > 0: # plot elevation profile
            rolling_elevation = pd.Series(elevation).rolling(3).mean()
            tour_info.gen_elevation_profile(rolling_elevation)

        sports[tour_info.activity_type].add_child(tour_info.gen_polyline())
        markersGroup.add_child(tour_info.gen_marker())

        time.sleep(0.2)

def parse_gpx(file_path):
    # parse gpx file to pandas dataframe
    gpx = gpxpy.parse(open(file_path), version='1.0')

    data = []
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point_idx, point in enumerate(segment.points):
                points.append(tuple([point.latitude, point.longitude]))

                # calculate distances between points
                if point_idx == 0:
                    distance = float('NaN')
                else:
                    distance = hs.haversine(
                        point1=points[point_idx-1],
                        point2=points[point_idx],
                        unit=hs.Unit.METERS
                    )

                data.append([point.longitude, point.latitude,point.elevation, point.time, segment.get_speed(point_idx), distance])

    columns = ['Longitude', 'Latitude', 'Elevation', 'Time', 'Speed', 'Distance']
    gpx_df = pd.DataFrame(data, columns=columns)

    return points, gpx_df

def get_timedelta(time_string):
    time_object = datetime.datetime.strptime(time_string, "%H:%M:%S")
    return datetime.timedelta(
        hours=time_object.hour,
        minutes=time_object.minute,
        seconds=time_object.second
    )

def genGarmin(sports, markersGroup):
    summaryCSV = 'garmin_connect_export/activities.csv'
    summaryDF = pd.read_csv(summaryCSV)
    for record in tqdm(summaryDF.iterrows(), desc="Plotting progress [Garmin]: ", total=summaryDF.shape[0]):
        id, record_info = record
        activity_id = record_info['Activity ID']
        activity_path = f'garmin_connect_export/activity_{activity_id}.gpx'

        if not os.path.exists(activity_path):
            print(f"No .gpx found for activity {activity_id}")
            continue
        points, gpx_df = parse_gpx(activity_path)


        tour_info = RouteInfo(
            record_info['Activity Name'],
            activity_id,
            'Garmin',
            record_info['Activity Parent'],
            record_info['Activity Type'],
            datetime.datetime.fromisoformat(record_info['Start Time']),
            points,
            record_info['Distance (km)'],
            record_info['Elevation Gain (m)'],
            get_timedelta(record_info['Duration (h:m:s)']),
            record_info['Average Speed (km/h)'],
            record_info['Max. Speed (km/h)'],
            0, # avg watts
            0, # max watts
        )

        if not tour_info.process_tour():
            print(f'\n{tour_info.get_debug_description()}: skipping as set as "not process"')
            continue
        if not tour_info.line:
            print(f'\n{tour_info.get_debug_description()}: skipping as no .gpx line found"')
            continue

        if len(gpx_df) > 0: # plot elevation profile
            rolling_elevation = gpx_df['Elevation'].rolling(3).mean()
            gpx_df['Cumulative Distance'] = gpx_df['Distance'].cumsum() / 1000.
            tour_info.gen_elevation_profile(rolling_elevation, gpx_df['Cumulative Distance'])

        sports[tour_info.activity_type].add_child(tour_info.gen_polyline())
        markersGroup.add_child(tour_info.gen_marker())

        time.sleep(0.2)


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

    # genStravaWithClass(activities, markersGroup, sports, gearDistanceElevationMap, gearMap)
    # genStrava(activities, markersGroup, gearDistanceElevationMap, gearMap, elevation_profile, sports)
    genGarmin(sports, markersGroup)

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
