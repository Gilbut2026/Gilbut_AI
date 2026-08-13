사용 방법

1) 이 폴더의 3개 파일을 route_scoring/tuning/label_studio/ 에 넣습니다.
2) Gilbut_AI 최상위 터미널에서 아래 한 줄만 실행합니다.

   bash route_scoring/tuning/label_studio/run_label_studio.sh

3) 처음 실행하면 Label Studio 설치 → 프로젝트 생성 → 120개 데이터 import → 서버 실행까지 자동으로 진행됩니다.
4) 브라우저에서 http://localhost:8080 으로 접속해 계정을 만들고 평가를 시작합니다.

평가 선택지는 경로 A / 비슷함 / 경로 B 입니다.
tradeoff_type은 분석용으로 task 데이터에만 포함되고 평가 화면에는 표시되지 않습니다.
