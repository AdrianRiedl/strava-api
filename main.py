import time
from datetime import datetime
from pathlib import Path
from tqdm import tqdm
import requests
import pandas as pd
import numpy as np
import folium
import polyline
import os
import time
from pypolyline.util import encode_coordinates, decode_polyline

from src.api_methods import get_methods
from src.api_methods import authorize
from src.data_preprocessing import main as data_prep

# used to f.e set the limit of fetched activities (default - 30)
ACTIVITIES_PER_PAGE = 200
# current page number with activities
PAGE_NUMBER = 1

GET_ALL_ACTIVITIES_PARAMS = {
    'per_page': ACTIVITIES_PER_PAGE,
    'page': PAGE_NUMBER
}


def main():
    token: str = authorize.get_acces_token()
    data: dict = get_methods.access_activity_data(token, params=GET_ALL_ACTIVITIES_PARAMS)
    df = data_prep.preprocess_data(data)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    df.to_csv(Path('data', f'my_activity_data={timestamp}.csv'), index=False)


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


# get you strava data
if os.path.isfile("activities.csv"):
    activities = pd.read_csv("activities.csv")
else:
    access_token: str = authorize.get_acces_token()
    max_number_of_pages = 10
    data = list()
    for page_number in tqdm(range(1, max_number_of_pages + 1)):
        page_data = get_data(access_token, page=page_number)
        if page_data == []:
            break
        data.append(page_data)

        # data dictionaries
    data_dictionaries = []
    for page in data:
        data_dictionaries.extend(page)
    # print number of activities
    print('Number of activities downloaded: {}'.format(len(data_dictionaries)))

    # normalize data
    activities = pd.json_normalize(data_dictionaries)
    activities.to_csv("activities.csv")
# sample activities
activities[['name', 'distance', 'average_speed', 'moving_time']].sample(5)
# add decoded summary polylines
t2 = polyline.decode('u{~vFvyys@fS]')
t = decode_polyline('u{~vFvyys@fS]'.encode(), 5)
activities = activities.dropna(subset=['map.summary_polyline'])
activities['map.polyline'] = activities['map.summary_polyline'].apply(polyline.decode)


# define function to get elevation data using the open-elevation API
def get_elevation(latitude, longitude):
    time.sleep(0.2)
    base_url = 'https://api.open-elevation.com/api/v1/lookup'
    payload = {'locations': f'{latitude},{longitude}'}
    r = requests.get(base_url, params=payload).json()
    return r['results'][0]['elevation']


# get elevation data
# elevation_data = list()
# for idx in tqdm(activities.index):
#     activity = activities.loc[idx, :]
#     # elevation = [get_elevation(coord[0], coord[1]) for coord in tqdm(activity['map.polyline'])]
#     # elevation_data.append(elevation)
#     # add elevation data to dataframe
# activities['map.elevation'] = elevation_data

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
        'map.summary_polyline',
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

# select one activity
# my_ride = activities.iloc[0, :]  # first activity (most recent)

# m = folium.Map()
# # for i in my_ride['map.polyline']:
# for index, row in activities.iterrows():
# # for i in range(0, len(activities)):
#     folium.PolyLine(locations=row['map.polyline']).add_to(m)
# m.save('route.html')
# plot ride on map
# centroid = [
#     np.mean([a for a, _ in my_ride['map.polyline'][0]]),
#     np.mean([b for _, b in my_ride['map.polyline'][0]])
# ]
# m = folium.Map(location=centroid, zoom_start=10)
# folium.PolyLine(my_ride['map.polyline'], color='red').add_to(m)
# m
# if __name__ == '__main__':
#     main()
# plot all activities on map
resolution, width, height = 75, 6, 6.5


def centroid(polylines):
    x, y = [], []
    for polyline in polylines:
        for coord in polyline:
            x.append(coord[0])
            y.append(coord[1])
    return [(min(x) + max(x)) / 2, (min(y) + max(y)) / 2]


m = folium.Map(location=(48.1372,11.5755), zoom_start=4)
# color scheme
color = {'Ride': 'red', 'Run': 'green', 'Hike': 'purple', 'Walk': 'purple', 'Swim': 'blue'}
icons = {'Ride': 'bicycle', 'Run': 'person', 'Hike': 'person', 'Walk': 'person', 'Swim': 'water'}
sports = {}
for c in color.keys():
    sports[c] = folium.FeatureGroup(name=c)
    sports[c].add_to(m)

for row in activities.iterrows():
    row_index = row[0]
    row_values = row[1]
    folium.PolyLine(row_values['map.polyline'], color=color[row_values['type']]).add_to(m)
    halfway_coord = row_values['map.polyline'][int(len(row_values['map.polyline']) / 2)]
    # popup text
    html = """
    <h3>{}</h3>
        <p>
            <code>
            Date : {} <br>
            Time : {} <br>
            <a href="https://www.strava.com/activities/{}" target="_blank">Activity</a>
            </code>
        </p>
    <h4>{}</h4>
        <p>
            <code>
                Distance&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.2f} km <br>
                Elevation Gain&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.0f} m <br>
                Moving Time&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {} <br>
                Average Speed&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.2f} km/h (maximum: {:.2f} km/h) <br>
                Average Watts&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp: {:.1f} W (maximum: {:.1f} W) <br>
            </code>
        </p>
    """.format(
        row_values['name'],
        row_index.date(),
        row_index.time(),
        row_values['id'],
        row_values['type'],
        row_values['distance'],
        row_values['total_elevation_gain'],
        time.strftime('%H:%M:%S', time.gmtime(row_values['moving_time'])),
        row_values['average_speed'], row_values['max_speed'],
        # row_values['average_cadence'],
        # row_values['average_heartrate'],
        # row_values['max_heartrate'],
        row_values['average_watts'], row_values['max_watts']
        # row_values['average_temp'],
        # row_values['kilojoules'],
        # row_values['suffer_score'],
        # row_values['athlete_count'],
        # row_values['kudos_count'],
        # elevation_profile[row_values['id']],
    )

    # add marker to map
    iframe = folium.IFrame(html, width=(width * resolution) + 20, height=(height * resolution) + 20)
    popup = folium.Popup(iframe, max_width=2650)
    icon = folium.Icon(color=color[row_values['type']], icon=icons[row_values['type']], icon_color="white", prefix='fa')

    marker = folium.Marker(location=halfway_coord, popup=popup, icon=icon)
    marker.add_to(m)

# Add dark and light mode.
# folium.TileLayer('cartodbdark_matter', name="dark mode", control=True).add_to(m)
# folium.TileLayer('cartodbpositron', name="light mode", control=True).add_to(m)
# We add a layer controller.
folium.LayerControl(collapsed=True).add_to(m)
m.save('route.html')
# display(m)
