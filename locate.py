import json
import math
import time
import paho.mqtt.client as mqtt
from datetime import datetime

class DeviceLocationTracker:
    def __init__(self, mqtt_broker="localhost", mqtt_port=1883):
        # Constants for conversion
        self.FEET_TO_METERS = 0.3048
        self.METERS_TO_FEET = 3.28084
        
        # Home dimensions in meters
        self.home_width = 27 * self.FEET_TO_METERS  # East-West (X-axis)
        self.home_length = 42 * self.FEET_TO_METERS  # North-South (Y-axis)
        self.floor_height = 10 * self.FEET_TO_METERS  # Z-axis
        
        # ESP32 positions in meters [x, y, z]
        # Origin (0,0,0) is at the southeast bottom corner
        self.esp32_positions = {
            # Ground Floor
            "espresense_readingroom": [10 * self.FEET_TO_METERS, 10 * self.FEET_TO_METERS, 5 * self.FEET_TO_METERS],
            "espresense_studio": [(self.home_length - 5) * self.FEET_TO_METERS, 6 * self.FEET_TO_METERS, 3 * self.FEET_TO_METERS],
            
            # First Floor
            "espresense_bedroom": [(self.home_length - 10) * self.FEET_TO_METERS, 5 * self.FEET_TO_METERS, 
                                 self.floor_height + 5 * self.FEET_TO_METERS],
            "espresense_amma": [(self.home_length - 10) * self.FEET_TO_METERS, (self.home_width - 4) * self.FEET_TO_METERS, 
                              self.floor_height + 2 * self.FEET_TO_METERS],
            "espresense_kitchen": [15 * self.FEET_TO_METERS, 6 * self.FEET_TO_METERS, 
                                 self.floor_height + 3 * self.FEET_TO_METERS],
            
            # Second Floor
            "espresense_theatre": [(self.home_length - 7) * self.FEET_TO_METERS, (self.home_width - 12) * self.FEET_TO_METERS,
                                 2 * self.floor_height + 2 * self.FEET_TO_METERS],
            "espresense_thetophat": [(self.home_length - 21) * self.FEET_TO_METERS, (self.home_width - 4) * self.FEET_TO_METERS,
                                   2 * self.floor_height + 10 * self.FEET_TO_METERS]
        }
        
        # Define room boundaries
        self.define_room_boundaries()
        
        # List of tracked devices
        self.device_names = {
            "irk:1f675efed04b065afa81b46b500cf042": "Su Watch",
            "irk:7aa501ab6bd5c2382aecff28ddfa1eee": "Sn iPhone",
            "irk:37112d6ca4debb67092ef94601af9318": "Su iPhone",
            "irk:48b4be2a5da9c3667670417d24d0b32f": "Sn Watch"
            # Add more devices as needed
        }
        
        # Store latest readings from each ESP32 for each device
        self.device_readings = {device_id: {} for device_id in self.device_names}
        
        # Store current location of each device
        self.device_locations = {device_id: {"room": "Unknown", "position": [0, 0, 0], "timestamp": datetime.now().isoformat()}
                               for device_id in self.device_names}
        
        # MQTT setup
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        
        # JSON file path
        self.json_file = "device_locations.json"
        
        # Signal attenuation factor due to floors/walls
        self.floor_attenuation = 1.5  # Additional meters of distance per floor difference
        self.wall_attenuation = 0.5   # Additional meters of distance per wall
        
        # Load existing location data if available
        try:
            with open(self.json_file, 'r') as f:
                self.device_locations = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.update_json_file()
    
    def define_room_boundaries(self):
        # Define room boundaries as [x_min, x_max, y_min, y_max, z_min, z_max] in meters
        self.rooms = {
            # Ground Floor (z: 0 to floor_height)
            "The Reading Space": [0, self.home_width/2, 0, self.home_length/2, 0, self.floor_height],
            "Studio": [0, self.home_width/2, self.home_length/2, self.home_length, 0, self.floor_height],
            "Ground Floor Hall": [self.home_width/2, self.home_width, 0, self.home_length, 0, self.floor_height],
            
            # First Floor (z: floor_height to 2*floor_height)
            "Bedroom": [0, 10*self.FEET_TO_METERS, 0, 15*self.FEET_TO_METERS, self.floor_height, 2*self.floor_height],
            "Amma Bedroom": [0, 10*self.FEET_TO_METERS, self.home_width-15*self.FEET_TO_METERS, self.home_width, 
                           self.floor_height, 2*self.floor_height],
            "Kitchen": [self.home_length-15*self.FEET_TO_METERS, self.home_length, 0, 10*self.FEET_TO_METERS, 
                      self.floor_height, 2*self.floor_height],
            "Hall": [10*self.FEET_TO_METERS, self.home_length-15*self.FEET_TO_METERS, 0, self.home_width, 
                   self.floor_height, 2*self.floor_height],
            
            # Second Floor (z: 2*floor_height to 3*floor_height)
            "Theatre": [self.home_length-20*self.FEET_TO_METERS, self.home_length, 0, 10*self.FEET_TO_METERS, 
                      2*self.floor_height, 3*self.floor_height],
            "Den": [self.home_length-20*self.FEET_TO_METERS, self.home_length, 10*self.FEET_TO_METERS, 20*self.FEET_TO_METERS, 
                  2*self.floor_height, 3*self.floor_height],
            "The Open Top": [0, self.home_length-20*self.FEET_TO_METERS, 0, self.home_width, 
                           2*self.floor_height, 3*self.floor_height]
        }
    
    def connect_mqtt(self):
        """Connect to the MQTT broker and start the loop"""
        print(f"Connecting to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}...")
        self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
        self.client.loop_start()
        
    def on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker"""
        print(f"Connected to MQTT broker with result code {rc}")
        
        # Subscribe to all ESP32 topics for tracked devices
        for device_id in self.device_names:
            for esp32_id in self.esp32_positions:
                # Extract ESP32 name (without the "espresense_" prefix)
                esp32_name = esp32_id.replace("espresense_", "")
                topic = f"espresense/devices/{device_id}/{esp32_name}"
                print(f"Subscribing to {topic}")
                client.subscribe(topic)
    
    def on_message(self, client, userdata, msg):
        """Callback when message is received from MQTT broker"""
        try:
            # Parse the message
            payload = json.loads(msg.payload.decode('utf-8'))
            
            # Extract the device ID and ESP32 name from the topic
            topic_parts = msg.topic.split('/')
            device_id = topic_parts[2]
            esp32_name = "espresense_" + topic_parts[3]
            
            # Check if this is a device we're tracking
            if device_id not in self.device_names:
                return
                
            # Store the reading
            if esp32_name in self.esp32_positions:
                self.device_readings[device_id][esp32_name] = {
                    "distance": payload.get("distance", 0),
                    "timestamp": datetime.now().isoformat()
                }
                
                # Triangulate position and update location
                self.update_device_location(device_id)
                
        except Exception as e:
            print(f"Error processing message: {e}")
    
    def update_device_location(self, device_id):
        """Triangulate the device position and determine its room"""
        readings = self.device_readings.get(device_id, {})
        
        # Need at least 3 readings for triangulation
        if len(readings) < 3:
            return
            
        # Filter out old readings (older than 60 seconds)
        now = datetime.now()
        valid_readings = {
            esp32_id: reading for esp32_id, reading in readings.items()
            if (now - datetime.fromisoformat(reading["timestamp"])).total_seconds() < 60
        }
        
        if len(valid_readings) < 3:
            return
            
        # Triangulate position using multilateration
        position = self.triangulate_position(device_id, valid_readings)
        
        # Determine which room the device is in
        room = self.determine_room(position)
        
        # Check if the room has changed
        if room != self.device_locations[device_id].get("room", "Unknown"):
            # Update device location
            self.device_locations[device_id] = {
                "room": room,
                "position": position,
                "timestamp": datetime.now().isoformat(),
                "friendly_name": self.device_names.get(device_id, "Unknown Device")
            }
            
            # Write to JSON file
            self.update_json_file()
            
            print(f"{self.device_names.get(device_id, device_id)} moved to {room}")
    
    def triangulate_position(self, device_id, readings):
        """Triangulate device position using weighted multilateration"""
        # Weighted average calculation
        total_weight = 0
        position = [0, 0, 0]
        
        for esp32_id, reading in readings.items():
            esp32_pos = self.esp32_positions[esp32_id]
            distance = reading["distance"]  # distance in meters
            
            # Calculate weight (closer ESP32s have more influence)
            weight = 1 / (distance ** 2) if distance > 0.1 else 100
            
            # Add weighted position estimate
            for i in range(3):
                position[i] += esp32_pos[i] * weight
                
            total_weight += weight
        
        # Normalize by total weight
        if total_weight > 0:
            position = [p / total_weight for p in position]
        
        # Adjust for floor boundaries
        if position[2] < self.floor_height * 0.5:
            position[2] = 0.5 * self.floor_height  # Ground floor
        elif position[2] < self.floor_height * 1.5:
            position[2] = 1.5 * self.floor_height  # First floor
        else:
            position[2] = 2.5 * self.floor_height  # Second floor
            
        return position
    
    def determine_room(self, position):
        """Determine which room the position is in"""
        for room_name, bounds in self.rooms.items():
            if (bounds[0] <= position[0] <= bounds[1] and
                bounds[2] <= position[1] <= bounds[3] and
                bounds[4] <= position[2] <= bounds[5]):
                return room_name
                
        return "Outside"
    
    def update_json_file(self):
        """Update the JSON file with current device locations"""
        try:
            with open(self.json_file, 'w') as f:
                json.dump(self.device_locations, f, indent=2)
        except Exception as e:
            print(f"Error updating JSON file: {e}")
    
    def run(self):
        """Run the tracker"""
        try:
            self.connect_mqtt()
            print("Device location tracker running. Press Ctrl+C to exit.")
            while True:
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("Stopping device location tracker...")
            self.client.loop_stop()
            self.client.disconnect()

if __name__ == "__main__":
    tracker = DeviceLocationTracker()
    tracker.run()
