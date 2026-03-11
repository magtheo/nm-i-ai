"""Presets for common observation patterns."""
from .core import Observer
from .output import ConsoleOutput, JSONOutput


def game_loop_observer(
    console_interval: int = 10,
    json_output: bool = True,
    output_dir: str = "observer_logs"
) -> Observer:
    """
    Create an observer configured for game loops.
    
    Usage:
        obs = game_loop_observer()
        for frame in game:
            with obs.session(f"frame_{frame}"):
                with obs.phase("update"):
                    update()
                with obs.phase("render"):
                    render()
    """
    outputs = [ConsoleOutput(interval=console_interval)]
    json_out = JSONOutput(output_dir=output_dir) if json_output else None
    
    return Observer()


def web_request_observer(
    output_dir: str = "observer_logs"
) -> Observer:
    """
    Create an observer configured for web request handling.
    
    Usage:
        obs = web_request_observer()
        
        @app.route("/")
        def handler():
            with obs.session(request.id):
                with obs.phase("auth"):
                    user = authenticate()
                with obs.phase("db"):
                    data = query_db()
                with obs.phase("render"):
                    return render(data)
    """
    return Observer()


def batch_processor_observer(
    output_dir: str = "observer_logs"
) -> Observer:
    """
    Create an observer configured for batch processing.
    
    Usage:
        obs = batch_processor_observer()
        
        with obs.session("batch_1"):
            for item in items:
                with obs.phase("process"):
                    process(item)
                obs.counter("items").increment()
    """
    return Observer()
