"""콘텐츠 트렌드 트래커 핵심 패키지."""

from __future__ import annotations

import sys

from src.services.ai_result_parser_v21_runtime import (
    install_ai_result_parser_v21_contract,
)
from src.services.content_pack_freshness_review_runtime import (
    install_content_pack_freshness_review_contract,
)
from src.services.portal_full_window_analysis_runtime import (
    install_portal_full_window_analysis_contract,
)
from src.services.topic_angle_model_fallback_runtime import (
    install_topic_angle_model_fallback_contract,
)
from src.services.topic_angle_partial_recovery_runtime import (
    install_topic_angle_partial_recovery_contract,
)
from src.services.trend_clustering_diagnostic_runtime import (
    install_trend_clustering_diagnostic_contract,
)
from src.services.trend_clustering_quality_runtime import (
    install_trend_clustering_quality_diagnostic_contract,
)
from src.services.trend_refresh_clustering_job_history_runtime import (
    install_refresh_clustering_job_history_contract,
)
from src.services.trend_source_review_runtime import (
    install_trend_source_review_contract,
)
from src.services.trend_source_visibility_policy_diagnostic_runtime import (
    install_trend_source_visibility_policy_diagnostic_contract,
)


# 새 AI 요청서의 SEO·무료 이미지 2중 확인 schema 2.1을 검사하면서
# 기존 저장 결과의 1.0·2.0 파싱 호환성은 그대로 유지합니다.
install_ai_result_parser_v21_contract()
# 모든 새 AI 요청서는 실제 답변 시점의 현재 날짜와 최신 웹 검색을 기준으로 삼고,
# 초안 작성 뒤 세 번의 추가 웹 재검증을 끝낸 뒤에만 최종 JSON을 출력합니다.
install_content_pack_freshness_review_contract()
# 앱·예약 수집·진단이 같은 분석 범위를 사용하도록 Streamlit 여부와 무관하게
# NAVER·Daum 최근 분석 시간 범위 전체 계약을 먼저 설치합니다.
install_portal_full_window_analysis_contract()
# Gemini 3.7 Flash 주제 방향은 같은 요청을 자동 재시도하지 않고, 일시적 서비스
# 오류·타임아웃일 때만 Gemini 3.6 Flash로 한 번 fallback해 무료 RPD를 보호합니다.
install_topic_angle_model_fallback_contract()
# 강한 YouTube·Google Trends·위키 신호는 모든 실행 경로에서 같은 기준으로
# 추천이 아닌 검토 후보까지만 승격합니다. 사실 근거 안전장치는 그대로 유지합니다.
install_trend_source_review_contract()
# 읽기 전용 출처 노출 진단의 보류 표본에는 현재 승격 정책을 그대로 적용해
# 품질·기회·관심 신호 중 실제로 막힌 지표를 함께 표시합니다.
install_trend_source_visibility_policy_diagnostic_contract()
# 최신 데이터 수집이 같은 프로세스에서 직접 수행한 2단계 군집도 별도 job/batch
# 원장에 남겨 이후 P2 품질 진단이 실제 최신 처리 결과를 기준으로 판단하게 합니다.
install_refresh_clustering_job_history_contract()
# HTTP 200 부분 응답은 기존 유효 결과를 유지하면서 누락·검증 탈락 ID만 한 번
# 보강하고, 보강 요청도 실제 요청 수량으로 Gemini 원장에 기록합니다.
install_topic_angle_partial_recovery_contract()
# P2 군집 진단도 Streamlit 여부와 무관하게 현재 토큰 분할 스냅샷 계약을 사용하고,
# 과거 고정 후보 수 작업 이력은 기존 방식으로 읽을 수 있게 유지합니다.
install_trend_clustering_diagnostic_contract()
# 최신 군집 작업 시각과 현재 군집 스냅샷이 일치할 때만 단독·다중·기존 연결 결과를
# 읽기 전용으로 재구성해 P2 품질 표본에 붙입니다.
install_trend_clustering_quality_diagnostic_contract()


# Streamlit 앱은 src 모듈보다 먼저 streamlit을 가져옵니다. 이 경로에서만
# 실제 앱의 군집 처리량·요청 압축·상단 보조 UI와 운영 로그 UI를 설치합니다.
if "streamlit" in sys.modules:
    from src.services.portal_full_window_analysis_runtime import (
        install_portal_full_window_streamlit_contract,
    )
    from src.services.program_log_runtime import install_program_logging_contract
    from src.services.source_collection_log_runtime import (
        install_source_collection_logging,
    )
    from src.services.post_collection_cleanup_runtime import (
        install_post_collection_cleanup_contract,
    )
    from src.services.program_log_correlation_runtime import (
        install_program_log_correlation_contract,
    )
    from src.services.topic_angle_candidate_diagnostic_service import (
        install_topic_angle_candidate_diagnostic_contract,
    )
    from src.services.web_update_launch_runtime import (
        install_web_update_launch_contract,
    )

    # 과거 NAVER·Daum 개수 상한 입력은 읽기 전용 안내로 바꾸고 저장 시 0(전체)을 기록합니다.
    install_portal_full_window_streamlit_contract(sys.modules["streamlit"])
    install_program_logging_contract()
    # 기존 대상 집계 로그를 유지하면서 바로 다음 행에 전체 후보·제외 사유를 기록합니다.
    install_topic_angle_candidate_diagnostic_contract()
    install_source_collection_logging()
    # 자동 정리는 출처별 저장 완료 뒤 순위 계산 직전에 실행합니다.
    install_post_collection_cleanup_contract()
    # 위 래퍼 전체를 실행 ID 문맥으로 감싸 세부 단계를 한 실행으로 묶습니다.
    install_program_log_correlation_contract()
    # web_update_ui가 함수를 직접 가져오기 전에 검증형 실행기로 교체합니다.
    install_web_update_launch_contract()

    from src.services.trend_cluster_runtime_contract import (
        install_clustering_settings_ui_contract,
        install_trend_cluster_runtime_contract,
    )
    from src.services.trend_stage_program_log_runtime import (
        install_precise_trend_stage_logging,
    )

    install_trend_cluster_runtime_contract()
    install_precise_trend_stage_logging()

    import src.services.trend_clustering_job_service as _clustering_job_module
    from src.services.trend_clustering_stale_display_runtime import (
        install_clustering_stale_display_contract,
    )
    from src.services.trend_cluster_progress_detail_runtime import (
        install_cluster_progress_detail_contract,
    )

    # DB 시간만으로 오래된 작업을 실패·중단으로 단정하지 않고, 수집·군집 잠금이
    # 모두 비활성으로 확인된 오래된 이력만 화면에서 상태 확인 대상으로 표시합니다.
    install_clustering_stale_display_contract(_clustering_job_module)
    install_cluster_progress_detail_contract(_clustering_job_module)

    import src.ui as _ui_module
    from src.services.content_pack_request_layout_runtime import (
        install_content_pack_request_layout_runtime,
    )
    from src.services.content_workflow_ui_runtime import (
        install_content_workflow_ui_runtime,
    )
    from src.trend_candidate_blog_recommendation_ui import (
        install_trend_candidate_blog_recommendation_ui,
    )
    from src.services.trend_blog_recommendation_ui_runtime import (
        install_trend_blog_recommendation_ui_runtime,
    )

    # AI 요청서 본문 폭과 ChatGPT 수동 전달 버튼의 배치를 현재 실사용 화면에 맞춥니다.
    install_content_pack_request_layout_runtime(_ui_module)
    # AI 결과 단계 버튼·편집 저장 규칙·HTML 미리보기는 기존 app 흐름을 보존한 채 보정합니다.
    install_content_workflow_ui_runtime(_ui_module)
    install_clustering_settings_ui_contract(_ui_module)
    install_trend_candidate_blog_recommendation_ui(_ui_module)
    install_trend_blog_recommendation_ui_runtime(st_module=sys.modules["streamlit"])

    from src.clustering_batch_log_ui import install_clustering_batch_log_ui
    from src.clustering_job_status_ui import install_clustering_job_status_ui
    from src.dashboard_background_refresh_ui import (
        install_dashboard_background_refresh,
    )
    from src.dashboard_operation_status_ui import (
        install_dashboard_operation_status_ui,
    )
    from src.log_display_format_ui import install_log_display_formatting
    from src.operational_logs_ui import install_operational_logs_ui
    from src.program_button_log_ui import install_program_button_logging
    from src.scheduler_wake_status_ui import install_scheduler_wake_status_ui
    from src.trend_auto_model_ui import install_trend_auto_model_ui
    from src.web_update_confirmation_ui import install_web_update_confirmation_ui
    from src.web_update_ui import install_web_update_top_navigation_ui

    # 수집 버튼의 rerun을 먼저 가로채 최신 데이터 수집도 2차 군집처럼
    # 별도 백그라운드 프로세스로 시작합니다.
    install_dashboard_background_refresh(sys.modules["streamlit"])
    # 군집 전용 래퍼보다 먼저 설치해 군집 캡션은 유지하면서 모든 로그 표에
    # 공통 숫자·시간 표시 규칙을 적용합니다.
    install_log_display_formatting(sys.modules["streamlit"])
    install_clustering_batch_log_ui(sys.modules["streamlit"])
    install_clustering_job_status_ui(sys.modules["streamlit"])
    install_operational_logs_ui(sys.modules["streamlit"])
    install_program_button_logging(sys.modules["streamlit"])
    install_scheduler_wake_status_ui(sys.modules["streamlit"])
    install_trend_auto_model_ui(sys.modules["streamlit"])
    install_web_update_confirmation_ui(sys.modules["streamlit"])
    install_web_update_top_navigation_ui(sys.modules["streamlit"])
    # 마지막에 설치해 실제 진행 파일을 기준으로 작업 버튼을 확실히 막고,
    # 기존 최근 실행 안내 위치를 접이식 단계 이력 패널로 교체합니다.
    install_dashboard_operation_status_ui(sys.modules["streamlit"])
