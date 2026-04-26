# Luguan

用于统计 `🦌` 表情次数的 AstrBot 插件。

## 功能

- 当用户发送的消息中包含 `🦌` 时，插件会按照消息中的 `🦌` 数量累加该用户当天的次数。
- 记录成功后，插件会发送一张该用户当月的月历图。
- 月历中没有记录的日期保持空白；有记录的日期会显示 `luguan.png` 和当天次数。
- 每月结束后，插件会自动向有记录的群聊发送上个月的 `🦌` 次数排行榜。
- 插件会尽量捕获运行时错误，避免单次处理失败导致插件崩溃。

## 数据存储

插件数据存储在：

```text
data/plugin_data/astrbot_plugin_luguan/
```

主要数据文件：

```text
data/plugin_data/astrbot_plugin_luguan/luguan_data.json
```

生成的月历图片存储在：

```text
data/plugin_data/astrbot_plugin_luguan/generated/
```

如果检测到旧数据文件 `luguan_stats.json`，插件会在首次启动时自动读取并迁移到新数据结构。

## 依赖

插件使用 Pillow 生成月历图片，依赖已写入 `requirements.txt`：

```text
pillow>=11.2.1
```

## 资源文件

请确保插件目录下存在：

```text
luguan.png
```

月历中有记录的日期会使用这张图片作为标记。
