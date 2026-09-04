"""
Over-the-counter sales. **Not finished, and deliberately closed.**

`SaleItem` does not move stock — creating one sells something the shelf still
believes it has — so until this is routed through `pharmacy/services.py` these
endpoints are admin-only rather than open to every signed-in user. No page
calls them; opening them up is part of building the checkout, not a
prerequisite for it.
"""
from rest_framework import viewsets
from apps.accounts.permissions import IsAdmin
from .models import Sale, SaleItem
from .serializers import SaleSerializer, SaleItemSerializer


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleSerializer
    permission_classes = [IsAdmin]

    def perform_create(self, serializer):
        serializer.save(sold_by=self.request.user)


class SaleItemViewSet(viewsets.ModelViewSet):
    queryset = SaleItem.objects.all()
    serializer_class = SaleItemSerializer
    permission_classes = [IsAdmin]
