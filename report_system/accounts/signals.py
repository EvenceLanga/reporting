from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def check_first_login(sender, request, user, **kwargs):
    if user.must_change_password:
        request.session['force_password_change'] = True
