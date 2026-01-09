from django.core.management.base import BaseCommand
from django_q.models import Schedule


class Command(BaseCommand):
    help = "Create Django-Q schedule for dashboard cache refresh"

    def handle(self, *args, **kwargs):
        if not Schedule.objects.filter(func="accounts.tasks.refresh_dashboard_cache").exists():
            Schedule.objects.create(
                func="accounts.tasks.refresh_dashboard_cache",
                schedule_type=Schedule.MINUTES,
                minutes=1,
                repeats=-1
            )
            self.stdout.write(self.style.SUCCESS("Schedule created."))
        else:
            self.stdout.write("Schedule already exists.")
