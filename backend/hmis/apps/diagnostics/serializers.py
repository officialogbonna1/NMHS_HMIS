from rest_framework import serializers
from .models import InvestigationCatalog, InvestigationOrder, InvestigationResult
class InvestigationCatalogSerializer(serializers.ModelSerializer):
    class Meta: model=InvestigationCatalog; fields="__all__"
class InvestigationOrderSerializer(serializers.ModelSerializer):
    class Meta: model=InvestigationOrder; fields="__all__"; read_only_fields=["requested_by","performed_by"]
class InvestigationResultSerializer(serializers.ModelSerializer):
    class Meta: model=InvestigationResult; fields="__all__"; read_only_fields=["released_by","released_at"]
