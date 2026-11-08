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


def test_local_backup_has_explicit_governance_scope_and_no_profile_data():
    from scripts.local_restore_drill import APP_TABLES,backup_command
    command=backup_command('postgres:17-alpine','/private/source.env','snapshot','export')
    assert '--schema=public' not in command
    assert '--exclude-table-data=public.profiles' in command
    assert not any(value.startswith('--schema=') for value in command)
    assert set(TABLES)<=set(APP_TABLES)
    assert all('--table=public.'+table in command for table in APP_TABLES)


@pytest.mark.parametrize('image',['unknown/postgres:17','postgres:latest','postgres:16','evil.example/postgres:17'])
def test_source_credentials_are_not_given_to_arbitrary_images(image):
    from scripts.local_restore_drill import validate_image
    with pytest.raises(ValueError):validate_image(image)


def test_restore_comparison_checks_additional_governance_tables_too():
    source=baseline();source['tables']['workflow_jobs']={'rows':1,'sha256':'jobs'}
    restored={**deepcopy(source),'database_identity_sha256':'isolated-target'}
    restored['tables']['workflow_jobs']['sha256']='changed'
    assert compare(source,restored)['state']=='failed'
