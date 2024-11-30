import datetime
import math
import matplotlib.pyplot as plt
import io
import base64

import folium
from gpxpy.geo import distance
from pydantic.v1.config import get_config

settings = {'Ride': {'color': 'red', 'icon': 'bicycle', 'process': True,
                     'subcategories': {'Ride': 0, 'GravelRide': 10, 'MountainBikeRide': 20}},
            'Run': {'color': 'green', 'icon': 'person', 'process': True,
                    'subcategories': {'Run': 0}},
            'Hike': {'color': 'purple', 'icon': 'person', 'process': True,
                     'subcategories': {'Hike': 0}},
            'Walk': {'color': 'purple', 'icon': 'person', 'process': True,
                     'subcategories': {'Walk': 0}},
            'Swim': {'color': 'blue', 'icon': 'water', 'process': True,
                     'subcategories': {'Swim': 0}},
            'Ski': {'color': 'orange', 'icon': 'person-skiing', 'process': True,
                    'subcategories': {'Ski': 0}},
            }

garmin2stravaTypes = {
    'Cycling': 'Ride',
    'Road Cycling': 'Ride',
    'Gravel Cycling': 'GravelRide',
    'Mountain Biking': 'MountainBikeRide',
}

popup_raw = """
        <h3>{}</h3>
            <p style="font-family:'Courier New'" font-size=30px>
                Date : {} <br>
                Time : {} <br>
                <a href="{}" target="_blank">Activity</a>
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
        """

def makeNaNZero(a):
    return a if math.isnan(a) else 0

class RouteInfo:
    activity_name: str
    activity_id: str
    activity_src: str

    activity_type: str
    activity_subtype: str

    start_time: datetime.datetime
    route_line = None

    distance: float
    elevation_gain: float
    moving_time: datetime.timedelta
    avg_speed: float
    max_speed: float

    avg_watt: float
    max_watt: float

    elevation_profile_plot = 'iVBORw0KGgoAAAANSUhEUgAAAHAAAAA4CAYAAAAl63xKAAAABHNCSVQICAgIfAhkiAAAABl0RVh0U29mdHdhcmUAZ25vbWUtc2NyZWVuc2hvdO8Dvz4AAAAtdEVYdENyZWF0aW9uIFRpbWUARnJpIDA4IE1hciAyMDI0IDEwOjQ4OjU4IEFNIENFVHmib7gAAACdSURBVHic7dHBCQAgEMAwdf+dzyF8SCGZoNA9M7PIOr8DeGNgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxBsYZGGdgnIFxF0PVBGzyjItLAAAAAElFTkSuQmCC'

    def __init__(self, activity_name, activity_id, activity_src,
                 activity_type, activity_subtype, start_time,
                 gpx_line, distance, elevation_gain, moving_time, avg_speed, max_speed, avg_watt, max_watt
                 ):
        self.activity_name = activity_name
        self.activity_id = activity_id
        self.activity_src = activity_src
        self.start_time = start_time
        self.line = gpx_line
        self.distance = distance
        self.elevation_gain = elevation_gain
        self.moving_time = moving_time
        self.avg_speed = avg_speed
        self.max_speed = max_speed
        self.avg_watt = avg_watt
        self.max_watt = max_watt

        if activity_src == 'Strava':
            self.activity_type = activity_type
            self.activity_subtype = activity_subtype
        elif activity_src == 'Garmin':
            self.activity_type = garmin2stravaTypes[activity_type]
            self.activity_subtype = garmin2stravaTypes[activity_subtype]
        else:
            raise f'Unknown Activity src: {activity_src}'


    def get_moving_time_pretty(self):
        total_secs = int(self.moving_time.total_seconds())
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        seconds = total_secs % 60
        return f"{hours:02}:{minutes:02}:{seconds:02}"


    def get_popup_text(self):
        return popup_raw.format(
            self.activity_name,
            self.start_time.strftime("%d.%m.%Y"),
            self.start_time.strftime("%H:%M:%S"),
            self.get_link(),
            self.activity_type,
            self.distance,
            self.elevation_gain,
            self.get_moving_time_pretty(),
            self.avg_speed,
            self.max_speed,
            makeNaNZero(self.avg_watt),
            makeNaNZero(self.max_watt),
            self.elevation_profile_plot
        )

    def get_color(self):
        return settings[self.activity_type]['color']

    def get_icon(self):
        return folium.Icon(
            color=self.get_color(),
            icon=settings[self.activity_type]['icon'],
            icon_color="white",
            prefix='fa',
        )

    def get_halfway_coordinate(self):
        return self.line[int(len(self.line) / 2)]

    def process_tour(self):
        return settings[self.activity_type]['process']

    def get_debug_description(self):
        return f'{self.activity_id} {self.activity_name} {self.activity_type}'

    def get_link(self):
        if self.activity_src == 'Strava':
            return f'https://www.strava.com/activities/{self.activity_id}'
        else:
            return None

    def gen_polyline(self):
        return folium.PolyLine(
            self.line,
            color=self.get_color(),
            popup=self.get_popup_text(),
            dash_array=settings[self.activity_type]['subcategories'].get(self.activity_subtype, 0)
        )

    def gen_marker(self):
        return folium.Marker(
            location=self.get_halfway_coordinate(),
            icon=self.get_icon()
        )

    def get_year(self):
        return self.start_time.year

    def get_month(self):
        return self.start_time.month

    def gen_elevation_profile(self, elevations, cumulative_distances=None):
        fig, ax = plt.subplots(figsize=(10, 4))
        if cumulative_distances is not None:
            ax.plot(cumulative_distances, elevations, color='steelblue')
            ax.set_xlabel('Distance [km]')
        else:
            ax.plot(range(len(elevations)), elevations, color='steelblue')
            ax.axes.xaxis.set_visible(False)

        ax.set_ylabel('Elevation')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

        # make the plot and convert it to base64
        pic_IObytes = io.BytesIO()
        plt.savefig(pic_IObytes, format='png', dpi=75)
        plt.close()
        pic_IObytes.seek(0)
        self.elevation_profile_plot = base64.b64encode(pic_IObytes.read()).decode()
