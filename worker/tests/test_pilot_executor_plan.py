from datetime import datetime,timezone
import pytest
from scripts.pilot_executor_plan import plan


def test_executor_plan_is_temporary_bucket_scoped_and_has_no_object_permissions():
    p=plan('pilot-project','123456789',120,datetime(2026,11,9,0,tzinfo=timezone.utc))
    assert p['bucket']=='aegis-assurance-pilot-123456789'
    assert p['permissions']==['storage.buckets.getIamPolicy','storage.buckets.setIamPolicy']
    assert p['condition']['expression']=='request.time < timestamp("2026-11-09T02:00:00Z")'
    assert p['member']=='serviceAccount:aegis-pipeline@pilot-project.iam.gserviceaccount.com'
    assert p['requires_explicit_operator_approval'] is True


@pytest.mark.parametrize('minutes',[0,121,True,1.5])
def test_executor_plan_cannot_request_unbounded_authority(minutes):
    with pytest.raises(ValueError):plan('pilot-project','123456789',minutes)


def test_executor_plan_does_not_accept_arbitrary_resource_identifiers():
    with pytest.raises(ValueError):plan('bad/project','123456789')
    with pytest.raises(ValueError):plan('pilot-project','*')
