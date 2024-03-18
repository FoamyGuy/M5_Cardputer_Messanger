# SPDX-FileCopyrightText: 2024 Tim Cocks
#
# SPDX-License-Identifier: MIT
import json
import os
import time
from time import monotonic
import board
import displayio
import microcontroller
import neopixel
import socketpool
import traceback
import terminalio
import wifi
import sdcardio
import storage
import rtc
import adafruit_ntp
from displayio import Group

from adafruit_display_text.bitmap_label import Label

from cardputer_lib import Cardputer
from adafruit_templateengine import render_template
from adafruit_httpserver import Server, Request, Response, Websocket, GET, POST, OK_200
from adafruit_displayio_layout.layouts.page_layout import PageLayout

from displayio_listselect import ListSelect

"""
TODO: Wishlist:
[x] show timestamps in the messages on the page
[x] show incoming messages on the handset screen
[x] clear the input message on the handset screen after it's sent
[x] allow handset to see list of users and send message to one without receiving first
[x] enter key on a user to go from list to write message
[ ] mobile version of the web page eventually
[ ] make a screen to view single conversation history. (re-use inbox UI?)
[ ] Control + number -> take user to that menu item even if they weren't on the menu
"""

pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, debug=True)

pixel = neopixel.NeoPixel(board.NEOPIXEL, 1)

websocket: Websocket = None
next_message_time = monotonic()

cardputer = Cardputer()

sdcard = sdcardio.SDCard(board.SD_SPI(), board.SD_CS, baudrate=20_000_000)
vfs = storage.VfsFat(sdcard)
storage.mount(vfs, "/sd")
time.sleep(0.2)

pool = socketpool.SocketPool(wifi.radio)
ntp = adafruit_ntp.NTP(pool, tz_offset=0)

# NOTE: This changes the system time so make sure you aren't assuming that time
# doesn't jump.
rtc.RTC().datetime = ntp.datetime

context = {
    "to_user": "another",
    "inbox_viewing_index": 0
}

try:
    os.stat("/sd/messages")
except OSError as e:
    print(traceback.format_exception(e))
    os.mkdir("/sd/messages/")

HTML_TEMPLATE = """
<html lang="en">
    <head>
        <title>Websocket Client</title>
    </head>
    <body>
        <p>CPU temperature: <strong>-</strong>&deg;C</p>
        <p>NeoPixel Color: <input type="color"></p>
        <script>
            const cpuTemp = document.querySelector('strong');
            const colorPicker = document.querySelector('input[type="color"]');

            let ws = new WebSocket('ws://' + location.host + '/connect-websocket');

            ws.onopen = () => console.log('WebSocket connection opened');
            ws.onclose = () => console.log('WebSocket connection closed');
            ws.onmessage = event => cpuTemp.textContent = event.data;
            ws.onerror = error => cpuTemp.textContent = error;

            colorPicker.oninput = debounce(() => ws.send(colorPicker.value), 200);

            function debounce(callback, delay = 1000) {
                let timeout
                return (...args) => {
                    clearTimeout(timeout)
                    timeout = setTimeout(() => {
                    callback(...args)
                  }, delay)
                }
            }
        </script>
    </body>
</html>
"""


def save_inbox_data(inbox_list):
    f = open("/sd/inbox.json", "w")
    f.write(json.dumps({"inbox": inbox_list}))
    f.close()


def load_inbox_data():
    f = open("/sd/inbox.json", "r")
    inbox_list = json.loads(f.read())["inbox"]
    f.close()
    return inbox_list


try:
    os.stat("/sd/inbox.json")
except OSError as e:
    print(traceback.format_exception(e))
    save_inbox_data([])

inbox = load_inbox_data()


@server.route("/client", GET)
def client(request: Request):
    return Response(request, HTML_TEMPLATE, content_type="text/html")


def read_data_for_user(username):
    f = open(f"/sd/messages/{username}.json", "r")
    _file_content = f.read()
    print(f"file content: {_file_content}")
    _ = json.loads(_file_content)
    f.close()
    return _


def save_data_for_user(username, data):
    f = open(f"/sd/messages/{username}.json", "w")
    f.write(json.dumps(data))
    f.close()


def prep_data_file(username):
    try:
        os.stat(f"/sd/messages/{username}.json")
    except OSError:
        f = open(f"/sd/messages/{username}.json", "w")
        f.write('{"messages": []}')
        f.close()


@server.route("/chat/<username>", (POST, GET), append_slash=True)
def chat(request, username):
    if request.method == "GET":
        prep_data_file(username)
        context["to_user"] = username
        print(os.listdir("/sd/messages/"))

        user_data = read_data_for_user(username)
        messages = user_data["messages"]
        for i in range(len(messages)):
            if messages[i]["data"] is not None:
                messages[i]["data"] = messages[i]["data"].replace("\n", "<br>")
        print(f"messages: {messages}")
        return Response(request, render_template(
            "/static/chat.tpl.html",
            context={"username": username, "messages": messages, "timestamp": time.time()},
        ), status=OK_200, content_type="text/html")
    elif request.method == "POST":
        prep_data_file(username)
        request_data = request.json()
        # print(request_data)
        # print(request_data["message"])
        new_msg_obj = {"data": request_data["message"], "time": time.time(), "to": 0}
        user_data = read_data_for_user(username)

        user_data["messages"].append(new_msg_obj)
        save_data_for_user(username, user_data)
        context["to_user"] = username

        inbox.append({"message_obj": new_msg_obj, "from": username})
        save_inbox_data(inbox)

        mail_icon_tg.hidden = False
        return Response(request, json.dumps({"success": True, "message": new_msg_obj}), status=OK_200,
                        content_type="text/plain")


@server.route("/connect-websocket", GET)
def connect_client(request: Request):
    global websocket  # pylint: disable=global-statement

    if websocket is not None:
        websocket.close()  # Close any existing connection

    websocket = Websocket(request)

    return websocket


def get_user_list():
    _users = []
    for _file in os.listdir("/sd/messages/"):
        _users.append(_file.replace(".json", ""))
    return _users


server.start(str(wifi.radio.ipv4_address))

write_title = Label(terminalio.FONT, text="Writing to: ")
write_title.anchor_point = (0, 0)
write_title.anchored_position = (6, 6)

input_lbl = Label(terminalio.FONT)
input_lbl.anchor_point = (0, 0)
input_lbl.anchored_position = (3, write_title.y + 10)

write_message_group = Group()
write_message_group.append(write_title)
write_message_group.append(input_lbl)

page_layout = PageLayout(x=0, y=0)

page_layout.add_content(write_message_group, "write")

main_menu_group = Group()
menu_title = Label(terminalio.FONT, text="Menu", scale=2)
menu_title.anchor_point = (0.5, 0)
menu_title.anchored_position = (board.DISPLAY.width // 2, 4)
main_menu_group.append(menu_title)

menu_body = """[1] List Users
[2] Write msg
[3] Inbox
[4] Conversation"""
menu_body = Label(terminalio.FONT, text=menu_body, scale=2)
menu_body.anchor_point = (0.5, 1.0)
menu_body.line_spacing = 1.0
menu_body.anchored_position = (board.DISPLAY.width // 2, board.DISPLAY.height - 4)
main_menu_group.append(menu_body)
page_layout.add_content(main_menu_group, "menu")

main_group = Group()
main_group.append(page_layout)

mail_icon = displayio.OnDiskBitmap("mail.bmp")
mail_icon.pixel_shader.make_transparent(0)
mail_icon_tg = displayio.TileGrid(bitmap=mail_icon, pixel_shader=mail_icon.pixel_shader)
mail_icon_tg.x = board.DISPLAY.width - 24 - 2
mail_icon_tg.y = 2
mail_icon_tg.hidden = True
main_group.append(mail_icon_tg)

inbox_group = Group()
inbox_title = Label(terminalio.FONT, text="Inbox", scale=2)
inbox_title.anchor_point = (0.5, 0)
inbox_title.anchored_position = (board.DISPLAY.width // 2, 4)
inbox_group.append(inbox_title)

inbox_username = Label(terminalio.FONT, scale=2)
inbox_username.anchor_point = (0, 0)
inbox_username.anchored_position = (4, 22)

print(f"size: {inbox_title.width}, {inbox_title.height}")
inbox_message = Label(terminalio.FONT, scale=2)
inbox_message.anchor_point = (0, 0)
inbox_message.anchored_position = (4, 42)

inbox_group.append(inbox_username)
inbox_group.append(inbox_message)

page_layout.add_content(inbox_group, "inbox")



conversation_group = Group()
conversation_title = Label(terminalio.FONT, text="Conversation", scale=2)
conversation_title.anchor_point = (0.5, 0)
conversation_title.anchored_position = (board.DISPLAY.width // 2, 4)
conversation_group.append(conversation_title)

conversation_username = Label(terminalio.FONT, scale=2)
conversation_username.anchor_point = (0, 0)
conversation_username.anchored_position = (4, 22)

print(f"size: {conversation_title.width}, {conversation_title.height}")
conversation_message = Label(terminalio.FONT, scale=2)
conversation_message.anchor_point = (0, 0)
conversation_message.anchored_position = (4, 42)

conversation_group.append(conversation_username)
conversation_group.append(conversation_message)

page_layout.add_content(conversation_group, "conversation")



list_users_group = Group()
list_users_title = Label(terminalio.FONT, text="Users", scale=2)
list_users_title.anchor_point = (0.5, 0)
list_users_title.anchored_position = (board.DISPLAY.width // 2, 4)
list_users_group.append(list_users_title)

list_users_select = ListSelect(scale=2, items=[])
list_users_select.anchor_point = (0, 0)
list_users_select.anchored_position = (4, 28)
list_users_select.visible_items_count = 4
list_users_select._label.line_spacing = 1.05
list_users_group.append(list_users_select)
page_layout.add_content(list_users_group, "list")

page_layout.show_page(page_name="menu")
board.DISPLAY.root_group = main_group

outgoing_message = None

if len(inbox) > 0:
    mail_icon_tg.hidden = False

try:
    while True:
        server.poll()

        # Check for incoming messages from client
        if websocket is not None:
            if (data := websocket.receive(True)) is not None:
                # no incoming data expected
                pass

        entered_text = cardputer.check_keyboard()
        if entered_text:
            if page_layout.showing_page_name == "menu":
                if entered_text == "1":
                    list_users_select.items = get_user_list()
                    page_layout.show_page(page_name="list")
                    continue
                elif entered_text == "2":
                    page_layout.show_page(page_name="write")
                    write_title.text = f"Writing to: {context['to_user']}"
                    continue
                elif entered_text == "3":
                    context["inbox_viewing_index"] = 0
                    if len(inbox) > 0:
                        inbox_username.text = inbox[0]["from"]
                        inbox_message.text = inbox[0]["message_obj"]["data"]

                        inbox_title.text = f"Inbox (1/{len(inbox)})"
                    else:
                        inbox_title.text = f"Inbox"

                    page_layout.show_page(page_name="inbox")
                    continue
                elif entered_text == "4":
                    page_layout.show_page(page_name="conversation")

            if page_layout.showing_page_name == "list":
                # if entered_text == "`":
                #     page_layout.show_page(page_name="menu")

                if entered_text == ".":  # Down
                    list_users_select.move_selection_down()

                elif entered_text == ";":  # Up
                    list_users_select.move_selection_up()

                elif entered_text == "\n":
                    print(f"selected: {list_users_select.selected_item}")
                    context["to_user"] = list_users_select.selected_item
                    write_title.text = f"Writing to: {context['to_user']}"
                    page_layout.show_page(page_name="write")
                    continue

            if page_layout.showing_page_name == "inbox":
                if entered_text == "\n":
                    _ = inbox.pop(context["inbox_viewing_index"])

                    if context["inbox_viewing_index"] >= len(inbox):
                        context["inbox_viewing_index"] = 0

                    save_inbox_data(inbox)
                    if len(inbox) > 0:
                        inbox_username.text = inbox[context["inbox_viewing_index"]]["from"]
                        inbox_message.text = inbox[context["inbox_viewing_index"]]["message_obj"]["data"]
                        inbox_title.text = f"Inbox ({context['inbox_viewing_index'] + 1}/{len(inbox)})"
                    else:
                        inbox_username.text = ""
                        inbox_message.text = ""
                        inbox_title.text = "Inbox"
                        mail_icon_tg.hidden = True
                if entered_text == " ":
                    context["inbox_viewing_index"] += 1
                    if context["inbox_viewing_index"] >= len(inbox):
                        context["inbox_viewing_index"] = 0
                    inbox_username.text = inbox[context["inbox_viewing_index"]]["from"]
                    inbox_message.text = inbox[context["inbox_viewing_index"]]["message_obj"]["data"]
                    inbox_title.text = f"Inbox ({context['inbox_viewing_index'] + 1}/{len(inbox)})"

                # if entered_text == "`":
                #     page_layout.show_page(page_name="menu")

            if entered_text == "ESC" or entered_text == "`":
                page_layout.show_page(page_name="menu")

            if page_layout.showing_page_name == "write":
                print(f"ctrl: {cardputer.ctrl}")

                print(f"et: {entered_text}")

                if entered_text == '\x08':
                    input_lbl.text = input_lbl.text[:-1]

                elif entered_text == "FN\n":
                    print("enter key with function")

                    # outgoing_message = input_lbl.text

                    prep_data_file(context["to_user"])
                    user_data = read_data_for_user(context["to_user"])
                    new_msg_obj = {"data": input_lbl.text, "time": time.time(), "to": 1}
                    outgoing_message = json.dumps(new_msg_obj)
                    user_data["messages"].append(new_msg_obj)
                    save_data_for_user(context["to_user"], user_data)

                    write_title.text = f"Sending to: {context['to_user']}"

                else:
                    input_lbl.text += entered_text

        # Send a message every second
        if websocket is not None and next_message_time < monotonic():
            if outgoing_message is not None:
                print(f"sending: {outgoing_message}")
                websocket.send_message(outgoing_message)
                outgoing_message = None
                write_title.text = f"Writing to: {context['to_user']}"
                input_lbl.text = ""
                page_layout.show_page(page_name="menu")
            next_message_time = monotonic() + 1

except Exception as e:
    print(traceback.format_exception(e))
    try:
        storage.umount("/sd")
    except OSError:
        pass
