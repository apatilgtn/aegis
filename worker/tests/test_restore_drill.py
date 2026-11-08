from copy import deepcopy
import pytest
from scripts.restore_drill import compare,TABLES


def baseline():
    return {'database_identity_sha256':'source','tables':{t:{'rows':1,'sha256':'a'} for t in TABLES},'writes_disabled':True,'rls_enabled':True,'captured_at':'2026-11-08T00:00:00Z'}


def test_restore_refuses_source_database_as_recovery_target():
    with pytest.raises(ValueError,match='different isolated'):compare(baseline(),baseline())


def test_matching_isolated_restore_reports_only_verified_scope():
    source=baseline();restored={**deepcopy(source),'database_identity_sha256':'isolated-target'}
    result=compare(source,restored)
    assert result['state']=='passed'
    assert any('not authentication' in limitation for limitation in result['limitations'])


@pytest.mark.parametrize('mutation',['data','rls','writes'])
def test_restore_mismatch_or_unsafe_controls_do_not_pass(mutation):
    source=baseline();restored={**deepcopy(source),'database_identity_sha256':'isolated-target'}
    if mutation=='data':restored['tables']['proposal_receipts']['sha256']='changed'
    elif mutation=='rls':restored['rls_enabled']=False
    else:restored['writes_disabled']=False
    assert compare(source,restored)['state']=='failed'


def test_changing_roles_or_pooler_host_cannot_disguise_source_database():
    from scripts.restore_drill import connection_identity
    project='abcdefghijklmnopqrst'
    pooled={'host':'pooler.example','user':'postgres.'+project,'dbname':'postgres'}
    another_role={**pooled,'user':'readonly.'+project,'host':'other-pooler.example'}
    direct={'host':'db.'+project+'.supabase.co','user':'postgres','dbname':'postgres'}
    assert connection_identity(pooled)==connection_identity(another_role)==connection_identity(direct)
    assert connection_identity({'host':'localhost','port':'5432','dbname':'a','user':'postgres'})==connection_identity({'host':'localhost','port':'5432','dbname':'a','user':'reader'})
