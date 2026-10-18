import json
from datetime import datetime, timedelta, timezone

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import UpstreamError, plan_trip


def _bad(message, status=400):
    return JsonResponse({"error": message}, status=status)


@csrf_exempt
@require_POST
def plan(request):
    try:
        body = json.loads(request.body)
        origin = str(body["origin"]).strip()
        destination = str(body["destination"]).strip()
        departure = datetime.fromisoformat(body["departure"])
        load_lb = float(body["load_lb"])
        interval_mi = int(body["interval_mi"])
    except (ValueError, KeyError, TypeError):
        return _bad("Send origin, destination, departure (ISO 8601), load_lb and interval_mi.")

    now = datetime.now(timezone.utc)
    if not origin or not destination:
        return _bad("Origin and destination are required.")
    if departure.tzinfo is None:
        return _bad("Departure must include a timezone offset.")
    if not now - timedelta(hours=1) <= departure <= now + timedelta(days=7):
        return _bad("Departure must be between now and 7 days ahead (forecast range).")
    if not 0 <= load_lb <= 80_000:
        return _bad("Load weight must be between 0 and 80,000 lb.")
    if interval_mi not in (10, 25, 50):
        return _bad("Sampling interval must be 10, 25 or 50 miles.")

    try:
        return JsonResponse(plan_trip(origin, destination, departure, load_lb, interval_mi))
    except UpstreamError as e:
        return _bad(str(e), status=502)
