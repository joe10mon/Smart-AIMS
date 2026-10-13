from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    # Django 5 only logs out on POST, so the logout control in the page is a small form.
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
]
