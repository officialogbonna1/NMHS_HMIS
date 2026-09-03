from rest_framework import serializers
from .models import Ward, Bed, Admission, BedTransfer, DischargeSummary
class WardSerializer(serializers.ModelSerializer):
    class Meta: model=Ward; fields="__all__"
class BedSerializer(serializers.ModelSerializer):
    occupied = serializers.SerializerMethodField()
    class Meta: model=Bed; fields="__all__"
    def get_occupied(self,obj): return obj.admissions.filter(status="admitted").exists()
class AdmissionSerializer(serializers.ModelSerializer):
    class Meta: model=Admission; fields="__all__"; read_only_fields=["admitted_by","status","admitted_at","discharged_at"]
class BedTransferSerializer(serializers.ModelSerializer):
    class Meta: model=BedTransfer; fields="__all__"; read_only_fields=["transferred_by"]
class DischargeSummarySerializer(serializers.ModelSerializer):
    class Meta: model=DischargeSummary; fields="__all__"; read_only_fields=["completed_by"]
