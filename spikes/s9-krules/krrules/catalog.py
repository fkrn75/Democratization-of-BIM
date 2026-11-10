"""요구사항 후보(REQUIREMENTS)와 구현된 검사 메타(CHECKS).

requirements.json 은 이 파일에서 build_catalog.py 로 만든다.
요약은 원문을 줄여 옮긴 것(원문 인용 없음)이고, 쪽수는 문서에 인쇄된 쪽 번호다
(조달청 본문은 인쇄 쪽 = PDF 쪽 - 10, 국토부 시행지침은 인쇄 쪽 = PDF 쪽).
"""

# 출처 약어 -> sources.json 의 id
PPS = "PPS-V21"            # 조달청 시설사업 BIM 적용지침서 v2.1(2022-12)
DES = "MOLIT-IMPL-DESIGN"  # 국토부 건설산업 BIM 시행지침 설계자 편(2022-07)
ORD = "MOLIT-IMPL-ORDERER"  # 국토부 건설산업 BIM 시행지침 발주자 편(2022-07)
BAS = "MOLIT-BASIC"        # 국토부 건설산업 BIM 기본지침(2020-12)


def R(rid, src, clause, page, summary, mode, ifc, checks=None, interp=False, duty="필수",
      why_not="", stage="실시설계", proxy=False):
    """proxy=True: 원문이 요구하는 것을 직접 검사하지 못하고 가까운 대리 지표로만 검사한다(해석)."""
    return {"id": rid, "source": src, "clause": clause, "page": page, "summary": summary,
            "mode": mode, "ifc": ifc, "checks": checks or [], "interpretation": interp,
            "proxy_indicator": proxy, "obligation": duty, "stage": stage,
            "status": "구현" if checks else "후보만", "not_implemented_reason": why_not}


REQUIREMENTS = [
    # ---- 형식·파일 ----
    R("REQ-001", PPS, "5.2.1(2), 5.5.2(1)", "83, 109", "BIM 데이터는 합의된 범위에서 IFC 2x3 이상 표준 포맷과 원본 포맷으로 제출", "코드",
      "파일 스키마(FILE_SCHEMA)", ["KR-CODE-01"]),
    R("REQ-002", ORD, "4.2(1)가", "64", "BIM 저작 소프트웨어는 IFC 2x3 이상을 지원", "코드", "FILE_SCHEMA, 헤더 originating_system", ["KR-CODE-01"]),
    R("REQ-003", PPS, "5.5.2(3)", "110", "납품 IFC 파일명은 건물번호(2자리)_건물명_구분(BA/BS/BM/BE/BC/BL/B/E).ifc", "코드",
      "파일 이름(IFC 안에는 없음)", ["KR-CODE-02"]),
    R("REQ-004", DES, "3.1(3)(가)", "88", "BIM 데이터 파일명은 영문·한글·숫자·'-'·'_' 만 사용(발주자편 3.6.3 p51, 부속서1 5.3 나 p66도 같은 규칙)", "코드", "파일 이름", ["KR-CODE-03"]),
    R("REQ-005", PPS, "5.2.2(5)다", "86", "BIM 데이터 단위는 밀리미터(mm) 원칙", "코드", "IfcProject 단위 지정(IfcUnitAssignment 의 LENGTHUNIT)", ["KR-CODE-04"]),
    R("REQ-006", DES, "2.3.2(1)(가)", "47", "BIM 데이터 단위는 m 또는 mm(ISO 십진 미터법). 기본지침도 같은 취지에 프로젝트 내 한 형식으로 통일", "코드",
      "IfcSIUnit LENGTHUNIT", ["KR-CODE-05"], duty="필수"),
    R("REQ-007", ORD, "부속서2 12번", "85", "원본 데이터가 200MB 를 넘으면 파일 분할을 검토하고 업로드 가능 용량인지 확인(조달청 5.5.3(3)라: IFC 는 300MB 초과 시 zip 허용). 원본 크기는 도구가 몰라 IFC 크기로 경고", "코드", "파일 크기(원본 200MB 분할 검토선, IFC 300MB 초과 시 zip 허용선을 메시지에 병기)",
      ["KR-CODE-16"], interp=True, duty="권고"),
    R("REQ-008", PPS, "5.2.2(7)가·나·다·라", "87", "분야별 파일 분리, 단일 건물은 단일 파일, 분리 파일은 합치면 일관, 분야별·건물별 통합 파일 모두 제출", "코드",
      "IfcBuilding 개수, 파일 세트", ["KR-CODE-15", "KR-CODE-18"], interp=True, duty="필수"),
    R("REQ-009", PPS, "5.5.3(1)", "111", "성과품 폴더 구성: BIM 보고서 / IFC(건물별 파일, 외피 선택) / 원본 / 도면", "코드", "폴더·파일 이름",
      ["KR-CODE-18"], interp=True),
    # ---- 공간 구조 ----
    R("REQ-010", PPS, "5.2.2(3)다, 5.2.2(4)나", "84-85", "공간 객체는 건축 BIM 데이터와 함께 작성(5.2.2(3)다). 최소 부위 작성 대상의 공간은 층, 구역, 실(5.2.2(4)나)", "IDS",
      "IfcBuildingStorey, IfcSpace(구역=IfcZone 은 선택)", ["KR-IDS-01", "KR-IDS-02"]),
    R("REQ-011", PPS, "5.2.2(6)가, 5.2.2(6)나", "86-87", "모든 공간·부위 객체는 특정 층에 소속. 여러 층에 걸쳐도 층 단위로 나눠 작성", "코드",
      "IfcRelContainedInSpatialStructure, IfcRelAggregates", ["KR-CODE-06"]),
    R("REQ-012", PPS, "5.2.2(6)라", "87", "지하층 이름은 'B' 또는 '지하' 로 시작", "코드", "Pset_BuildingStoreyCommon.AboveGround=FALSE(우선), IfcBuildingStorey.Name. Elevation<0 은 AboveGround 없을 때만 경고급 보조(기초층 제외)", ["KR-CODE-07"], interp=True),
    R("REQ-013", PPS, "5.2.2(6)다", "87", "층 범위는 해당 층 바닥 구조체 윗면 ~ 위층 바닥 구조체 윗면, 최하층 바닥·기초는 독립된 한 층", "불가",
      "IfcBuildingStorey.Elevation, IfcSlab 형상", why_not="바닥 구조체 윗면 높이를 기하로 재서 층 고도와 대조해야 함. 이번 범위에서 만들지 않음(코드로 가능)"),
    # ---- 공간 정보 ----
    R("REQ-014", PPS, "부속서-1 3.1(1)(2)", "부속서-1 p.2", "공간 객체에 실명 입력", "IDS", "IfcSpace.LongName", ["KR-IDS-03"], interp=True),
    R("REQ-015", PPS, "부속서-1 3.1(1)(3)", "부속서-1 p.2", "공간 객체에 실ID 입력(실번호와 별개). 가이드는 룸/영역에 실ID 사용자 속성을 입력하게 함", "IDS+코드",
      "이름이 '실ID' 인 속성(Pset 불문, 우선) 또는 IfcSpace.Name(보조)", ["KR-IDS-04"], interp=True),
    R("REQ-016", PPS, "부속서-1 3.1(3)가", "부속서-1 p.2", "실ID 형식 AA.BBB.CC (AA=시설-구역 01~89, 공용 98). 필요하면 변형 가능", "코드",
      "'실ID' 속성(우선) 또는 IfcSpace.Name(보조)의 값", ["KR-CODE-08"], interp=True, duty="권고"),
    R("REQ-017", PPS, "부속서-1 3.1(1)(4), 3.2", "부속서-1 p.2-7", "공간분류코드 입력(3.2 목록의 5자리 세분류)", "IDS+코드",
      "IfcRelAssociatesClassification, IfcClassificationReference.Identification 또는 '공간분류코드' 속성", ["KR-IDS-05", "KR-CODE-09"], interp=True),
    R("REQ-018", PPS, "5.2.2(3)바, 5.3(4)가", "84, 96", "공간 객체끼리 겹치지 않음(측면 경계는 벽중심선·벽내부선 두 세트를 모두 납품하므로 세트 안에서만 비교)", "코드",
      "IfcSpace 형상(같은 실ID·실명 쌍의 포함 관계로 두 세트 분리, 못 나누면 위반)", ["KR-CODE-10"], interp=True),
    R("REQ-019", PPS, "5.2.3(1)", "88", "공간 상부면=위층 바닥 밑면, 하부면=해당 층 바닥면, 측면=벽 중심선 또는 내부선", "불가", "IfcSpace 형상, IfcSlab 형상",
      why_not="경계 면 대조 기하 해석 필요(코드로 가능, 이번 범위 밖)"),
    R("REQ-020", PPS, "5.2.3(2)가", "88", "시설-구역 코드별로 서로 다른 색상(예: 01 살구, 02 주황, 98 파랑, 99 보라)", "불가", "IfcStyledItem/IfcSurfaceStyle",
      why_not="색상 추출·코드별 대응이 필요(코드로 가능하나 스타일 표현이 도구마다 달라 이번 범위 밖)", duty="권고"),
    R("REQ-021", PPS, "5.2.3(2)나, 부속서-1 3.1(5)", "88-89", "위층이 열린 공간은 층별로 나누고 가장 아래층 외에는 실명을 'OPEN' 으로", "불가",
      "IfcSpace.LongName", why_not="'위층이 열린 공간' 판정은 기하 추론이 필요"),
    # ---- 부위 정보 ----
    R("REQ-022", PPS, "5.2.2(4)바, 부속서-1 4.2(2)", "86", "구조·건축 부위 객체에 부재명 입력(속성 항목은 BIM 소프트웨어 기본 기능에 위임)", "IDS+코드",
      "Pset_*Common.Reference, ObjectType, 유형 객체 Name, 사용자 속성(부재명·유형 주석) 중 하나. 인스턴스 Name 은 쓰지 않음", ["KR-IDS-07"], interp=True),
    R("REQ-023", PPS, "부속서-1 4.2(3), 4.3", "부속서-1 p.14-18", "부위 객체에 조달청표준공사코드 입력", "코드", "사용자 정의 속성 '조달청표준공사코드'(Pset 이름 불문) 또는 Pset '조달청표준공사코드-세부공종'(ArchiCAD)", ["KR-CODE-19"], interp=True),
    R("REQ-024", PPS, "부속서-1 4.3(2)-(4)", "부속서-1 p.14-18", "공사코드 구문: 단일 'D', 복합 'D+E', 물량 구분 'D:200+FA11'", "코드", "속성 이름이 '조달청표준공사코드' 일 때의 값(ArchiCAD 세부공종 값은 구문 검사 제외)", ["KR-CODE-13"], interp=True),
    R("REQ-025", PPS, "5.2.2(4)마, 부속서-1 5", "85-86", "소프트웨어 제약으로 범용 객체로 만든 부위는 건설정보분류체계 부위분류 코드를 입력하고 결과보고서에 기록", "IDS+코드",
      "IfcBuildingElementProxy + 분류 참조 또는 속성 '건설정보분류체계-부위분류'", ["KR-IDS-06"], interp=True),
    R("REQ-026", PPS, "5.2.5(2)", "90-91", "창호는 벽에 소속되도록 작성(벽 먼저, 그 벽에 창호)", "코드", "IfcRelFillsElement -> IfcOpeningElement -> IfcRelVoidsElement -> IfcWall", ["KR-CODE-14"]),
    R("REQ-027", PPS, "5.2.2(4)다", "85", "모든 부위 객체는 구분해 작성(기둥과 보를 한 객체로 합치지 않음)", "불가", "객체 분리 여부", why_not="모델링 의도 판정, 기계 판별 불가(부분 신호: 한 객체의 형상이 둘 이상 부재 형상인지)"),
    R("REQ-028", PPS, "5.2.2(4)라", "85", "모든 부위 객체는 해당 부위 작성 기능으로 작성(기둥은 기둥 기능). 대리 지표: 작성 기능 사용 여부는 IFC 에서 알 수 없어, 범용객체(IfcBuildingElementProxy)가 분류 정보를 갖는지(KR-IDS-06)만 본다", "IDS+코드", "IfcBuildingElementProxy + 분류 참조 또는 부위분류 속성(범용객체 개수는 노트로만 노출, 비율은 검사하지 않음)", ["KR-IDS-06"], interp=True, duty="권고", proxy=True),
    R("REQ-029", PPS, "5.2.2(4)나", "85", "최소 부위 작성 대상 존재: 구조(기초·기둥·보·벽·바닥·지붕·계단), 건축(벽·문·창·커튼월·천장·난간·지붕이차구조)", "IDS",
      "IfcFooting, IfcColumn, IfcBeam, IfcWall, IfcSlab, IfcRoof, IfcStair, IfcDoor, IfcWindow, IfcCurtainWall, IfcCovering, IfcRailing", [],
      why_not="업무수행계획서로 범위를 조정할 수 있어 '모든 클래스 필수' 로 고정하면 오탐. 대신 KR-IDS-07, KR-CODE-19 가 존재하는 객체에만 요구. 별도 IDS 템플릿으로는 쓸 수 있음", duty="조건부"),
    # ---- 에너지·외피 ----
    R("REQ-030", PPS, "5.2.5(4)나", "91", "에너지 검토(환경 시뮬레이션) 수행 시에만: 바닥·벽체·지붕·기둥·문·창문·커튼월 7종 중 외기에 면한 객체는 IsExternal=True", "IDS",
      "Pset_*Common.IsExternal", ["KR-IDS-08"], duty="조건부"),
    R("REQ-031", PPS, "5.2.5(4)나", "91", "에너지 검토 수행 시에만: 외기에 면한 바닥·벽체·지붕·문·창문·커튼월 6종에는 열관류율 정보(부속서-1 4.2는 열관류율을 환경시뮬레이션 수행 시 선택사항으로 적음)", "IDS", "Pset_*Common.ThermalTransmittance", ["KR-IDS-09"], duty="조건부"),
    R("REQ-032", DES, "2.3.2 건축 ③", "56", "환경 시뮬레이션 수행 시에만: 외기에 면한 벽체·지붕·문·창문·커튼월 5종에 열관류율(선택 사항). 조달청은 바닥을 더한 6종이라 두 문서가 다름", "IDS",
      "Pset_*Common.ThermalTransmittance", ["KR-IDS-10"], duty="선택"),
    R("REQ-033", PPS, "5.2.5(4)가", "91", "건물 내부와 외부에 공기가 통하는 뚫린 곳이 없도록 모델링, 내벽·외벽 분리", "불가", "외피 폐합성", why_not="외피 밀폐 판정은 별도 기하 해석 필요(IDS 불가, 코드도 이번 범위 밖)"),
    # ---- 사업정보 ----
    R("REQ-034", PPS, "부속서-1 2 사업정보", "부속서-1 p.1", "사업명 입력(가이드는 프로젝트 정보에 사업명 속성)", "IDS+코드", "IfcProject.Name/LongName 또는 '사업명' 속성", ["KR-IDS-13"], interp=True),
    R("REQ-035", PPS, "부속서-1 2 사업정보", "부속서-1 p.1", "대지현황: 주소", "IDS+코드", "IfcSite.SiteAddress(AddressLines 비어 있지 않음) 또는 '주소' 속성", ["KR-IDS-12"], interp=True),
    R("REQ-036", PPS, "부속서-1 2 사업정보", "부속서-1 p.1", "건축규모: 용도, 층수(예: 지하4층, 지상10층)", "IDS+코드",
      "용도: Pset_BuildingCommon.OccupancyType 또는 '용도' 속성. 층수: NumberOfStoreys(정수 하나) 또는 '층수' 속성 또는 층 개체 수", ["KR-IDS-14", "KR-IDS-15"], interp=True),
    R("REQ-037", PPS, "부속서-1 2 사업정보", "부속서-1 p.1", "건축규모: 건축면적, 연면적. 대지현황: 대지면적", "IDS+코드",
      "Qto_BuildingBaseQuantities(FootprintArea/GrossFloorArea), Qto_SiteBaseQuantities.GrossArea; IFC2X3 대체 Pset_BuildingCommon.GrossPlannedArea, Pset_SiteCommon.TotalArea; 사용자 속성 건축면적·연면적·대지면적(IFC2X3 도 판정, 값 존재만 확인)",
      ["KR-IDS-16", "KR-IDS-17", "KR-IDS-18"], interp=True),
    R("REQ-038", PPS, "부속서-1 2 사업정보", "부속서-1 p.1", "대지현황: 지역지구, 도로현황", "불가", "(표준 속성 없음)", why_not="IFC 표준 Pset 에 대응 속성이 없어 사용자 정의 Pset 약속이 먼저 필요"),
    # ---- 좌표 ----
    R("REQ-039", PPS, "5.2.7(2)가", "93", "대지 좌표는 임시수준점(TBM) 기준, GIS 좌표는 위도·경도와 TM 좌표계", "IDS", "IfcSite.RefLatitude/RefLongitude", ["KR-IDS-11"], interp=True),
    R("REQ-040", PPS, "5.2.7(2)다, DES 2.3.2(1)(나)", "93, 48", "BIM 데이터 기준점의 상대 평면좌표(X,Y)·지반레벨·진북 방향각 관리", "코드", "IfcGeometricRepresentationContext.TrueNorth(기본값 (0,1)이면 의도 불명 노트), IfcMapConversion(IFC2X3 은 ePSet_MapConversion), IfcSite.RefElevation 유무는 노트", ["KR-CODE-11"], interp=True),
    R("REQ-041", DES, "2.3.2(1)(나)", "47-48", "측량 기준계는 GRS80 + TM(서부·중부·동부·동해 원점). 단 발주자가 정하면 상대 기준 좌표계도 허용(p48). 기본지침은 통상 UTM52 계열", "코드", "IfcProjectedCRS, IfcMapConversion(IFC2X3 은 ePSet_ProjectedCRS, ePSet_MapConversion 속성세트. 둘 다 없으면 판정하지 않음)", ["KR-CODE-12"], interp=True, duty="권고"),
    # ---- 품질 ----
    R("REQ-042", PPS, "5.3(4)나", "96", "부위 객체 간 간섭 금지. 구조-설비 간섭 불허, 그 외 허용오차 10mm", "코드", "객체 형상 쌍 교차", [], why_not="지오메트리 교차 계산(OCC)이 필요, 구조·설비 분야 파일 쌍이 있어야 의미. 이번 범위 밖(코드로 가능)"),
    R("REQ-043", PPS, "5.3(4)다", "96", "기초 위 기둥·보·바닥 등 구조 부재는 다른 구조 부재가 지지", "불가", "IfcRelConnects*, 형상 접촉", why_not="지지 관계 그래프 + 형상 접촉 판정, 이번 범위 밖"),
    R("REQ-044", ORD, "부속서2 5번", "84", "불필요한 정보 제거: 숨김 객체 삭제, 객체 중복 없음, 불필요한 저장 뷰 제거", "코드", "GlobalId 중복, 같은 위치·크기 중복", ["KR-CODE-17"], interp=True, duty="권고"),
    R("REQ-045", ORD, "부속서2 10번", "85", "원본 대비 IFC 변환 수량 검토(객체 수 일치)", "불가", "원본 모델과 IFC 비교", why_not="원본(rvt 등) 없이는 판정 불가. 골든 JSON 과 대조하는 방식은 S0 표본 전용"),
    R("REQ-046", DES, "4.2.3 속성데이터 품질", "103-104", "필수 속성정보 누락·오타, 표준분류체계 기준 속성 유무 검토", "IDS", "(IDS 전반)", ["KR-IDS-07", "KR-CODE-19"], interp=True),
    R("REQ-047", PPS, "5.2.2(5)가·나, 부속서-2", "86", "상세수준은 BIL 40 기본으로 협의, 전체 동일 적용", "불가", "BIL 표 대비 형상 상세도", why_not="BIL 표(부속서-2) 기준 형상 평가 절차 필요. 미분석"),
    R("REQ-048", PPS, "부속서-7 3(2)", "부속서-7 p.2-3", "관급자재 BIM 라이브러리에 부위분류(3자리)·조달청표준공사코드(3자리)·제작자·제작일(YYYYMMDD) 입력, IFC 2x3 이상 병행 제작", "코드",
      "IfcElementType, 분류 참조, Pset_ManufacturerTypeInformation", [], why_not="라이브러리 파일 범위. 이번 시범은 건물 모델만"),
    R("REQ-049", DES, "3.3(3)(라)", "93", "IFC 파일은 압축 관리를 원칙으로 함(압축 형식은 기관이 정함)", "불가", "(파일 형식)", why_not="기관별 압축 형식이 미정이라 판정 기준 없음"),
    R("REQ-050", PPS, "5.2.4~5.2.7 (1)", "89-92", "분야별 BIM 데이터는 해당 분야 부위 객체로만 구성(건축=건축 부위+공간, 기계=기계 부위 등)", "코드", "클래스 분포 + 파일 구분(_BA/_BS/_BM/_BE)", [],
      why_not="분야 구분을 파일명 접미사에 의존. 파일명 규칙을 따르는 납품 세트가 있을 때 구현 가능(이번에는 안 함)"),
    R("REQ-051", BAS, "모델링 필수 요구사항(단위·좌표계)", "PDF 40", "BIM 모델에 좌표계·표고를 정해야 하며 국내는 통상 UTM84-52S(UTM 52 구역)로 지정. 시행지침(2022)의 GRS80 TM 과 표현이 다름", "코드",
      "IfcProjectedCRS, IfcMapConversion(IFC2X3 은 ePSet_*). 52S 는 UTM 위도 띠 표기로 보고 북반구 52구역 EPSG:32652 로 해석", ["KR-CODE-12"], interp=True, duty="권고"),
    R("REQ-052", BAS, "성과품 파일 종류와 포맷", "PDF 49", "BIM 모델 성과품은 IFC 포맷과 원본 포맷을 기본으로 포함(발주자 요구가 있으면 IFC 변환 제출). 교환 표준 포맷은 IFC", "코드",
      "FILE_SCHEMA", ["KR-CODE-01"]),
]

# 구현된 검사. mode: ids | code. severity: 필수 | 조건부 | 권고 | 참고
# 조건부 = 원문이 "에너지 검토(환경 시뮬레이션) 수행 시"로 한정한 규칙(KR-IDS-08/09/10). 실행기는 --energy 일 때만 판정한다.
CHECKS = {
    "KR-IDS-01": ("ids", "필수"), "KR-IDS-02": ("ids", "필수"), "KR-IDS-03": ("ids", "필수"),
    "KR-IDS-04": ("ids", "필수"), "KR-IDS-05": ("ids", "필수"), "KR-IDS-06": ("ids", "필수"),
    "KR-IDS-07": ("ids", "필수"), "KR-IDS-08": ("ids", "조건부"),
    "KR-IDS-09": ("ids", "조건부"), "KR-IDS-10": ("ids", "조건부"), "KR-IDS-11": ("ids", "필수"),
    "KR-IDS-12": ("ids", "필수"), "KR-IDS-13": ("ids", "필수"), "KR-IDS-14": ("ids", "필수"),
    "KR-IDS-15": ("ids", "필수"), "KR-IDS-16": ("ids", "필수"), "KR-IDS-17": ("ids", "필수"),
    "KR-IDS-18": ("ids", "필수"),
    "KR-CODE-01": ("code", "필수"), "KR-CODE-02": ("code", "필수"), "KR-CODE-03": ("code", "필수"),
    "KR-CODE-04": ("code", "필수"), "KR-CODE-05": ("code", "필수"), "KR-CODE-06": ("code", "필수"),
    "KR-CODE-07": ("code", "필수"), "KR-CODE-08": ("code", "권고"), "KR-CODE-09": ("code", "필수"),
    "KR-CODE-10": ("code", "필수"), "KR-CODE-11": ("code", "필수"), "KR-CODE-12": ("code", "권고"),
    "KR-CODE-13": ("code", "필수"), "KR-CODE-14": ("code", "필수"), "KR-CODE-15": ("code", "권고"),
    "KR-CODE-16": ("code", "권고"), "KR-CODE-17": ("code", "권고"), "KR-CODE-18": ("code", "필수"),
    "KR-CODE-19": ("code", "필수"),
}

# 에너지 검토를 켰을 때만 판정하는 규칙
ENERGY_ONLY = sorted(cid for cid, (_, sev) in CHECKS.items() if sev == "조건부")
