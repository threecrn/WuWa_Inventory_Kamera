class EchoScanner:
    def __init__(self, controller, screenInfo):
        self.controller = controller
        self.screenInfo = screenInfo

    def scanEchoes(self, session_id):
        logging.error("Starting echo scanner — session=%s", session_id)
        scans = []
        for page in range(self.screenInfo.echoes.pages):
            for row in range(self.screenInfo.echoes.rows):
                for col in range(self.screenInfo.echoes.cols):
                    index = page * (self.screenInfo.echoes.rows * self.screenInfo.echoes.cols) + row * self.screenInfo.echoes.cols + col
                    if index >= self.screenInfo.echoes.total:
                        logging.error(