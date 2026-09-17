from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden

from .models import TenantMembership, current_tenant


class TenantContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(("/admin/", "/accounts/", "/static/")):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), "/accounts/login/")

        memberships = TenantMembership.objects.filter(
            user=request.user,
            tenant__is_active=True,
        ).select_related("tenant")
        selected_id = request.session.get("tenant_id")
        membership = memberships.filter(tenant_id=selected_id).first() if selected_id else None
        membership = membership or memberships.first()
        if membership is None:
            return HttpResponseForbidden("Your account is not assigned to a business workspace.")

        request.tenant = membership.tenant
        request.tenant_membership = membership
        request.session["tenant_id"] = membership.tenant_id
        token = current_tenant.set(membership.tenant)
        try:
            return self.get_response(request)
        finally:
            current_tenant.reset(token)