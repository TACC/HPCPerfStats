from django.contrib.auth.models import User, Group
from rest_framework import serializers
from stampede.models import Job
from stampede import stampedeapiviews
class UserSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = User
        fields = ('url', 'username', 'email', 'groups')


class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ('url', 'name')

class JobSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Job
        fields = ('id', 'project', 'start_time','end_time','start_epoch','end_epoch','run_time','queue','name','status','nodes','cores','path','date','user','exe','cwd','threads')

class JobDetailSerializer(serializers.HyperlinkedModelSerializer):
    master_plot = serializers.SerializerMethodField("get_master_plot")
    heat_map = serializers.SerializerMethodField("get_heat_map")
    sys_plot = serializers.SerializerMethodField("get_sys_plot")
    type_list = serializers.SerializerMethodField("get_type_list")
    def get_master_plot(self,obj):
        return stampedeapiviews.master_plot(None, obj.id)

    def get_heat_map(self,obj):
        return stampedeapiviews.heat_map(None, obj.id)

    def get_sys_plot(self,obj):
        return stampedeapiviews.sys_plot(None, obj.id)

    def get_type_list(self, obj):
        return stampedeapiviews.type_list(obj.id)

    class Meta:
        model = Job
        fields = ('id', 'project', 'start_time','end_time','start_epoch','end_epoch','run_time','queue','name','status','nodes','cores','path','date','user','exe','cwd','threads','master_plot','heat_map','sys_plot','type_list')
