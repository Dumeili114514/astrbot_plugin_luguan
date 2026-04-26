# Luguan

AstrBot plugin for counting the `🦌` emoji.

## Features

- When a user sends a message containing `🦌`, the plugin adds the number of `🦌` emojis in that message to the user's count for the current day.
- After recording, the plugin sends a calendar image for that user and month. Days without records are blank; days with records show `luguan.png` and the daily count.
- At 00:05 after the last day of each month, the plugin sends the previous month's ranking to each group where records exist.

## Data

Plugin data is stored in:

```text
data/plugin_data/astrbot_plugin_luguan/
```

Generated images are stored in:

```text
data/plugin_data/astrbot_plugin_luguan/generated/
```
