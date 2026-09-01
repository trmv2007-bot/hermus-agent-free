"""Realistic Android fixtures: a valid PNG screenshot and a uiautomator XML dump.

These are used by the ADB transport tests so that the transport is exercised with
*screen-accurate* payloads (real PNG bytes, real schema) rather than placeholders.
They are fixtures, not mocks of the tool path.
"""
from __future__ import annotations

import base64

# A 1x1 transparent PNG (valid signature + IHDR + IDAT + IEND, ending with \x1a\n).
PNG_1x1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# Realistic uiautomator `dump` payload: a Tasks screen with a title, an input field,
# and two buttons (Add/Clear), plus an existing task row.
UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" width="1080" height="2000" package="com.example.tasks"
          activity=".MainActivity">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example.tasks" content-desc="" checkable="false"
        checked="false" clickable="false" enabled="true" focusable="false"
        focused="false" scrollable="false" long-clickable="false"
        password="false" selected="false" bounds="[0,0][1080,2000]">
    <node index="0" text="Tasks" resource-id="app_title" class="android.widget.TextView"
          package="com.example.tasks" content-desc="" checkable="false"
          checked="false" clickable="false" enabled="true" focusable="false"
          focused="false" scrollable="false" long-clickable="false"
          password="false" selected="false" bounds="[0,80][1080,160]"/>
    <node index="1" text="Task title" resource-id="task_label"
          class="android.widget.TextView" package="com.example.tasks"
          content-desc="" checkable="false" checked="false" clickable="false"
          enabled="true" focusable="false" focused="false" scrollable="false"
          long-clickable="false" password="false" selected="false"
          bounds="[20,200][1060,260]"/>
    <node index="2" text="" resource-id="task_input" class="android.widget.EditText"
          package="com.example.tasks" content-desc="" checkable="false"
          checked="false" clickable="true" enabled="true" focusable="true"
          focused="true" scrollable="false" long-clickable="false"
          password="false" selected="false" bounds="[20,280][1060,400]"/>
    <node index="3" text="Add" resource-id="add" class="android.widget.Button"
          package="com.example.tasks" content-desc="" checkable="false"
          checked="false" clickable="true" enabled="true" focusable="true"
          focused="false" scrollable="false" long-clickable="false"
          password="false" selected="false" bounds="[20,420][520,520]"/>
    <node index="4" text="Clear" resource-id="clear" class="android.widget.Button"
          package="com.example.tasks" content-desc="" checkable="false"
          checked="false" clickable="true" enabled="true" focusable="true"
          focused="false" scrollable="false" long-clickable="false"
          password="false" selected="false" bounds="[560,420][1060,520]"/>
    <node index="5" text="Buy milk" resource-id="task_0"
          class="android.widget.FrameLayout" package="com.example.tasks"
          content-desc="" checkable="false" checked="false" clickable="false"
          enabled="true" focusable="false" focused="false" scrollable="false"
          long-clickable="false" password="false" selected="false"
          bounds="[20,560][1060,640]"/>
  </node>
</hierarchy>
"""
