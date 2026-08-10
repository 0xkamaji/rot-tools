from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gui import rot_say


def sr_status(args):
    url = "https://signalrot.net"
    request = Request(url, headers={"User-Agent": "rotbot/1.0"})
    started_at = perf_counter()

    try:
        with urlopen(request, timeout=10) as response:
            elapsed_ms = round((perf_counter() - started_at) * 1000)
            rot_say(
                "SIGNAL ROT STATUS\n"
                "-----------------\n"
                f"Site:     {url}\n"
                "State:    ONLINE\n"
                f"HTTP:     {response.status}\n"
                f"Response: {elapsed_ms} ms"
            )
    except HTTPError as error:
        elapsed_ms = round((perf_counter() - started_at) * 1000)
        rot_say(
            "SIGNAL ROT STATUS\n"
            "-----------------\n"
            f"Site:     {url}\n"
            "State:    ERROR\n"
            f"HTTP:     {error.code}\n"
            f"Response: {elapsed_ms} ms"
        )
    except (URLError, TimeoutError) as error:
        rot_say(
            "SIGNAL ROT STATUS\n"
            "-----------------\n"
            f"Site:     {url}\n"
            "State:    OFFLINE\n"
            f"Reason:   {error.reason if isinstance(error, URLError) else error}"
        )


def sr_pull(args):
    rot_say("Pulling latest signalrot version")


def sr_push(args):
    rot_say("Pushing latest signalrot version")


def sr_publish(args):
    rot_say("Publishing latest version of signalrot!")
