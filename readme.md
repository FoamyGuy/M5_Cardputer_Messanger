## Introduction
This is a simple web server that allows sending messages to and from
a cardputer device. One user holds the cardputer and the other user 
loads a page hosted by the webserver running in the cardputer. 

Messages are sent via HTTP Requests and Websockets.

#### **Warning: All messages are sent in clear text over HTTP. Anyone who can access the network traffic can see the messages.** 

### Requirements:
- Properly formatted SDCard inserted into device
- WIFI connection specified in settings.toml and web workflow enabled
- These Libraries
  - adafruit_httpserver
  - adafruit_templateengine
  - adafruit_displayio_layout
  - adafruit_display_text
  - displayio_listselect
  - adafruit_ntp
  - neopixel
