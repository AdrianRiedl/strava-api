import base64
import io
import json
import math
import sys
import time
from tqdm import tqdm
import requests
import pandas as pd
import folium
import polyline
import os
import time
import matplotlib.pyplot as plt
from folium.plugins import HeatMap

from src.api_methods import authorize


# define function to return NaN as 0
def makeNaNZero(a):
    if math.isnan(a):
        return 0
    return a


# define function to get your strava data
def get_data(access_token, per_page=200, page=1):
    activities_url = 'https://www.strava.com/api/v3/athlete/activities'
    headers = {'Authorization': 'Bearer ' + access_token}
    params = {'per_page': per_page, 'page': page}

    data = requests.get(
        activities_url,
        headers=headers,
        params=params
    ).json()

    return data


# download the data from the strava website
def downloadStravaData():
    print("Downloading from Strava")
    access_token: str = authorize.get_acces_token()
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
    payload = {'locations': []}
    for latitude, longitude in vec:
        payload['locations'].append({"latitude": latitude, "longitude": longitude})
    r = requests.post(url="https://api.open-elevation.com/api/v1/lookup",
                      headers={
                          "Accept": "application/json",
                          "Content-Type": "application/json; charset=utf-8",
                      },
                      data=json.dumps(payload)).json()
    return [] if 'results' not in r else [entry['elevation'] for entry in r['results']]


def runPreprocessing(localActivities):
    # convert data types
    localActivities.loc[:, 'start_date'] = pd.to_datetime(localActivities['start_date']).dt.tz_localize(None)
    localActivities.loc[:, 'start_date_local'] = pd.to_datetime(localActivities['start_date_local']).dt.tz_localize(
        None)
    # convert values
    localActivities.loc[:, 'distance'] /= 1000  # convert from m to km
    localActivities.loc[:, 'average_speed'] *= 3.6  # convert from m/s to km/h
    localActivities.loc[:, 'max_speed'] *= 3.6  # convert from m/s to km/h
    # set index
    localActivities.set_index('start_date_local', inplace=True)
    # drop columns
    localActivities.drop(
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
    return localActivities


def main(refreshDownload):
    if not os.path.isfile('activities.csv') or refreshDownload:
        data_dictionaries = downloadStravaData()
        # normalize data
        activities = pd.json_normalize(data_dictionaries)
        # store it as a csv file
        activities.to_csv("activities.csv")
    else:
        activities = pd.read_csv('activities.csv')

    # get the activities
    def getDelta(cached, new):
        outer = new.merge(cached, how='outer', on='id', indicator=True)
        anti = outer[(outer._merge == 'left_only')].drop('_merge', axis=1)
        return anti

    m = folium.Map(location=(48.1372, 11.5755), zoom_start=4)
    # # add full screen button
    folium.plugins.Fullscreen().add_to(m)

    # color scheme
    settings = {'Ride': {'color': 'red', 'icon': 'bicycle', 'process': True},
                'Run': {'color': 'green', 'icon': 'person', 'process': True},
                'Hike': {'color': 'purple', 'icon': 'person', 'process': True},
                'Walk': {'color': 'purple', 'icon': 'person', 'process': True},
                'Swim': {'color': 'blue', 'icon': 'water', 'process': True}}
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

    # plot all activities on map
    resolution, width, height = 75, 6, 6.5

    for row in tqdm(activities.iterrows(), desc="Plotting progress", total=activities.shape[0]):
        row_index = row[0]
        row_values = row[1]
        type = row_values['type']
        # option to skip specific activity types
        if not settings[type]['process']:
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
                print(f"Retrying for {row_values['id']}")
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
            makeNaNZero(row_values['average_watts']), makeNaNZero(row_values['max_watts']),
            elevation_profile[row_values['id']],
        )

        # add marker to map
        icon = folium.Icon(color=settings[type]['color'],
                           icon=settings[type]['icon'], icon_color="white", prefix='fa')

        # plot the activity
        l = folium.PolyLine(line, color=settings[type]['color'], popup=html)
        sports[type].add_child(l)

        marker = folium.Marker(location=halfway_coord, icon=icon)
        markersGroup.add_child(marker)
        time.sleep(0.2)

    # Add dark and light mode.
    # folium.TileLayer('cartodbdark_matter', name="dark mode", control=True).add_to(m)
    # folium.TileLayer('cartodbpositron', name="light mode", control=True).add_to(m)

    # We add a layer controller.
    folium.LayerControl(collapsed=True).add_to(m)
    m.save('route.html')


def printHelp():
    print("Usage: python3 main.py [--refresh]")
    print("It downloads the data from Strava and visualizes it.")
    print("It only downloads the data if no 'activity.csv' file exists or if the flag '--refresh' is set.")


if __name__ == '__main__':
    if '--help' in sys.argv or '-h' in sys.argv:
        printHelp()
        exit(0)
    refreshDownload = True if '--refresh' in sys.argv else False

    main(refreshDownload)
