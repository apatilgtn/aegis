from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
import pytest
from worker import gcp,native_analysis


def test_native_query_is_bounded_to_reviewed_identity_role_and_project(monkeypatch):
    calls=[]
    def call(method,url):
        calls.append((method,url))
        if url.startswith('https://cloudresourcemanager.googleapis.com/'):return {'projectNumber':'12345'}
        return {'fullyExplored':True,'mainAnalysis':{'fullyExplored':True,'analysisResults':[]}}
    monkeypatch.setattr(gcp,'_call',call)
    report=gcp.analyze_access('project-test','user:person@example.com','roles/logging.viewer')
    query=parse_qs(urlsplit(calls[-1][1]).query)
    assert query['analysisQuery.identitySelector.identity']==['user:person@example.com']
    assert query['analysisQuery.resourceSelector.fullResourceName']==['//cloudresourcemanager.googleapis.com/projects/project-test']
    assert query['analysisQuery.accessSelector.roles']==['roles/logging.viewer']
    assert query['executionTimeout']==['10s']
    assert report['binding_count']==0 and report['state']=='available'
    assert any('does not prove' in text for text in report['limitations'])
    assert all(method=='GET' for method,url in calls)


def test_native_partial_conditions_and_permission_errors_are_never_clean_success(monkeypatch):
    monkeypatch.setattr(gcp,'_call',lambda *args:{'fullyExplored':False,'mainAnalysis':{'fullyExplored':False,'nonCriticalErrors':[{'code':'PERMISSION_DENIED'}],'analysisResults':[{'iamBinding':{'role':'roles/storage.objectUser','condition':{'expression':'some condition'},'members':['user:private-other@example.com']},'accessControlLists':[{'conditionEvaluation':{'evaluationValue':'CONDITIONAL'}}]}]}})
    report=gcp.analyze_access('project-test','user:person@example.com','roles/storage.objectUser','reviewed-bucket')
    assert report['state']=='partial'
    assert report['condition_evaluations']==['CONDITIONAL']
    assert report['error_codes']==['PERMISSION_DENIED']
    assert 'private-other' not in str(report)


def test_native_provider_failure_is_unavailable_and_simulation_stays_unsupported():
    def denied(*args):raise PermissionError('denied')
    report=native_analysis.assess(SimpleNamespace(analyze_access=denied),{'project_id':'project-test'},{'role_identifier':'roles/logging.viewer'},'person@example.com')
    assert report['analyzer']['state']=='unavailable'
    assert report['simulator']['state']=='unsupported'
    assert report['advisory'] is True


def test_native_missing_route_does_not_query_an_unreviewed_target():
    report=native_analysis.assess(SimpleNamespace(),{'project_id':'project-test'},None,'person@example.com')
    assert report['analyzer']['state']=='unavailable'


@pytest.mark.parametrize('member,role,bucket',[('user:bad\nquery','roles/logging.viewer',None),('user:a@example.com','roles/logging.viewer','bad/bucket')])
def test_native_query_rejects_unreviewed_input(member,role,bucket):
    with pytest.raises(gcp.GcpError):gcp.analyze_access('project-test',member,role,bucket)


def test_omitted_default_false_fields_remain_partial_evidence(monkeypatch):
    monkeypatch.setattr(gcp,'_call',lambda *args:{'mainAnalysis':{'analysisResults':[]}})
    report=gcp.analyze_access('project-test','user:person@example.com','roles/storage.objectUser','reviewed-bucket')
    assert report['state']=='partial'
    assert report['fully_explored'] is False
