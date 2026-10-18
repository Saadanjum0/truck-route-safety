from django.urls import path

from planner.views import plan

urlpatterns = [path("api/plan", plan)]
