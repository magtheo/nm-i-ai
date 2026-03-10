# Observer

A simple but powerful observation tool for Python. Track performance metrics, detect bottlenecks, and optimize your code.

## Features

- **Zero dependencies** - Pure Python, works anywhere
- **Zero overhead when disabled** - `Observer(enabled=False)`
- **Generic API** - Works with any Python project
- **Automatic bottleneck detection** - Finds slow phases automatically
- **Multiple output formats** - Console, JSON, or custom handlers
- **Session-based tracking** - Organize metrics by sessions

## Installation

Just copy the `observer` folder to your project:

```
your_project/
├── observer/
│   ├── __init__.py
│   ├── core.py
│   ├── metrics.py
│   ├── output.py
│   ├── analysis.py
│   └── presets.py
└── your_code.py
```

## Quick Start

```python
from observer import Observer

# Create observer
obs = Observer()

# Time phases
with obs.phase("database"):
    result = db.query()

with obs.phase("compute"):
    processed = process(result)

# Track counters
obs.counter("requests").increment()
obs.counter("items_processed").increment(5)

# Track gauges (values that go up and down)
obs.gauge("queue_size").set(42)
obs.gauge("active_connections").increment()

# Analyze results
analysis = obs.analyze()
analysis.print_report()
```

## Sessions

Organize metrics into sessions (e.g., per request, per frame, per game):

```python
with obs.session("request_1"):
    with obs.phase("auth"):
        authenticate()
    with obs.phase("db"):
        query_database()
    obs.counter("requests").increment()

# Sessions are tracked separately
sessions = obs.get_sessions()
```

## Bottleneck Detection

```python
analysis = obs.analyze()

# Get bottlenecks (phases taking >30% of time)
for b in analysis.bottlenecks():
    print(f"{b.phase}: {b.avg_time_ms:.1f}ms ({b.percentage:.0f}% of time)")
    print(f"  Suggestion: {b.suggestion}")
```

## Output Handlers

### Console Output
```python
from observer import Observer, ConsoleOutput

obs = Observer(output_handler=ConsoleOutput(interval=10))
# Prints summary every 10 sessions
```

### JSON Output
```python
from observer import Observer, JSONOutput

json_out = JSONOutput(output_dir="logs")
obs = Observer(output_handler=json_out)

# After done:
analysis = obs.analyze()
json_out.save(analysis.to_dict())
# Saves to logs/observer_TIMESTAMP.json
```

## API Reference

### Observer

```python
Observer(enabled=True, output_handler=None)
```

| Method | Description |
|--------|-------------|
| `phase(name)` | Context manager to time a code block |
| `session(name, **metadata)` | Context manager for a session |
| `counter(name)` | Get/create a counter |
| `gauge(name)` | Get/create a gauge |
| `timer(name)` | Get/create a timer |
| `record(key, value)` | Record custom metadata |
| `analyze()` | Get Analysis object |
| `reset()` | Clear all metrics |
| `to_dict()` | Export all data as dict |

### Counter

```python
counter = obs.counter("requests")
counter.increment()      # +1
counter.increment(5)     # +5
counter.decrement()      # -1
print(counter.value)     # Current value
```

### Gauge

```python
gauge = obs.gauge("connections")
gauge.set(42)            # Set to 42
gauge.increment()        # +1
gauge.decrement()        # -1
print(gauge.value)       # Current value
print(gauge.avg)         # Average of all set() values
```

### Timer

```python
timer = obs.timer("custom")
timer.start()
# ... do work ...
duration = timer.stop()
print(timer.total)       # Total time in ms
print(timer.avg)         # Average duration
print(timer.count)       # Number of calls
```

### Analysis

```python
analysis = obs.analyze()
analysis.bottlenecks()   # List of Bottleneck objects
analysis.summary()       # Dict summary
analysis.print_report()  # Print formatted report
analysis.to_dict()       # Export as dict
```

## Examples

### Game Loop
```python
obs = Observer()

for frame in game_loop():
    with obs.session(f"frame_{frame}"):
        with obs.phase("input"):
            handle_input()
        with obs.phase("update"):
            update_game()
        with obs.phase("render"):
            render_frame()
        
        obs.gauge("fps").set(current_fps)

obs.analyze().print_report()
```

### Web Server
```python
from flask import Flask
from observer import Observer

app = Flask(__name__)
obs = Observer()

@app.route("/")
def index():
    with obs.session("index"):
        with obs.phase("db"):
            data = query_db()
        with obs.phase("render"):
            html = render_template("index.html", data=data)
        obs.counter("page_views").increment()
    return html
```

### Batch Processing
```python
obs = Observer()

with obs.session("import_batch"):
    for record in records:
        with obs.phase("parse"):
            parsed = parse(record)
        with obs.phase("validate"):
            validate(parsed)
        with obs.phase("save"):
            save(parsed)
        obs.counter("records").increment()

obs.analyze().print_report()
```

## License

MIT License - Free to use and modify.
