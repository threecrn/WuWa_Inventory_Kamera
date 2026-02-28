#!/usr/bin/env python3
"""
CLI script to update game data files without GUI dependencies.
Downloads data from WutheringData repository and processes it.
"""

import re
import json
import urllib.request
import logging
from babel import Locale
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('DatabaseUpdater')


@dataclass
class FileConfig:
    folder: list
    file: str


class CLIDataUpdater:
    """CLI version of DataUpdater without GUI/PySide6 dependencies"""

    API = 'https://api.github.com/repos/{owner}/{repo}/contents/{path}'

    def __init__(self, lang: Optional[str] = None):
        self.author = 'Dimbreath'
        self.repo = 'WutheringData'
        self.lang = lang or self._getLanguage()
        self.files = [
            FileConfig(['TextMap', self.lang], 'MultiText.json'),
            FileConfig(['ConfigDB'], 'ItemInfo.json'),
            FileConfig(['ConfigDB'], 'WeaponConf.json'),
        ]
        self.updated = False

    def _getLanguage(self) -> str:
        """Determine and cache the language from available options"""
        self.makeFolder()

        url = self.API.format(
            owner=self.author,
            repo=self.repo,
            path='TextMap'
        )
        
        languages = self.loadJson('languages.json')
        
        if not languages:
            # Download available languages
            logger.info('Fetching available languages...')
            try:
                items = self.fetchFileData(url)
                languages = {
                    self._getLanguageName(item['name']): item['name']
                    for item in items if item.get('type') == 'dir'
                }
                self.saveJson(languages, 'languages.json')
                logger.info(f'Available languages: {", ".join(languages.keys())}')
            except Exception as e:
                logger.error(f'Failed to fetch languages: {e}')
                return 'en'

        return languages.get('English', 'en')

    def makeFolder(self):
        """Ensure data directory exists"""
        Path('data').mkdir(parents=True, exist_ok=True)
        logger.debug("Ensured 'data' directory exists.")

    def _getLanguageName(self, code: str) -> str:
        """Convert language code to display name (e.g., 'en-US' -> 'English')"""
        try:
            parts = code.split('-')
            locale = Locale(parts[0], script=parts[1] if len(parts) > 1 else None)
            return locale.get_display_name().capitalize()
        except Exception:
            return code

    def fetchFileData(self, url: str) -> Any:
        """Fetch data from GitHub API"""
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logger.error(f'Failed to fetch data from {url}: {e}')
            return {}

    def updateFiles(self):
        """Download files from repository if they have changed"""
        for fileConfig in self.files:
            url = self.API.format(
                owner=self.author,
                repo=self.repo,
                path='/'.join(fileConfig.folder + [fileConfig.file])
            )

            logger.info(f'Checking for updates on file: {fileConfig.file}')
            try:
                data = self.fetchFileData(url)
                filePath = Path('data') / fileConfig.file

                if not data:
                    logger.warning(f'No data received for {fileConfig.file}')
                    continue

                currentSize = filePath.stat().st_size if filePath.is_file() else 0

                if data.get('size', 0) != currentSize:
                    logger.info(f'Downloading updated version of {fileConfig.file}...')
                    try:
                        urllib.request.urlretrieve(
                            data['download_url'],
                            filePath
                        )
                        self.updated = True
                        logger.info(f'File updated: {fileConfig.file} ({data.get("size", 0)} bytes)')
                    except Exception as e:
                        logger.error(f'Failed to download {fileConfig.file}: {e}')
                else:
                    logger.info(f'{fileConfig.file} is up to date')

            except Exception as e:
                logger.error(f'Failed to process {fileConfig.file}: {e}')

    def loadJson(self, filename: str) -> Dict:
        """Load JSON file from data directory"""
        try:
            with open(f'./data/{filename}', 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def saveJson(self, data: Dict, filename: str):
        """Save JSON file to data directory"""
        try:
            with open(f'./data/{filename}', 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            logger.debug(f'Saved {filename}')
        except Exception as e:
            logger.error(f'Failed to save {filename}: {e}')

    def updateItems(self):
        """Generate items.json and weapons.json from downloaded data"""
        if (Path('data') / 'items.json').is_file():
            logger.info('items.json already exists, skipping generation')
            return

        logger.info('Generating items.json and weapons.json...')
        try:
            infoText = self.loadJson('MultiText.json')
            itemInfo = self.loadJson('ItemInfo.json')
            weaponInfo = self.loadJson('WeaponConf.json')

            if not all([infoText, itemInfo, weaponInfo]):
                logger.error('Missing required data files for item generation')
                return

            items = {
                infoText[item['Name']].lower().replace(' ', ''): {
                    'id': item['Id'],
                    'name': infoText[item['Name']],
                    'image': item['Icon'].split('/Image/')[1].rsplit('.', 1)[0] + '.png'
                }
                for item in itemInfo if item['Name'] in infoText
            }

            weapons = {
                infoText[weapon['WeaponName']].lower().replace(' ', ''): {
                    'id': weapon['ModelId'],
                    'name': infoText[weapon['WeaponName']],
                    'rarity': weapon['QualityId'],
                    'image': weapon['Icon'].split('/Image/')[1].rsplit('.', 1)[0] + '.png'
                }
                for weapon in weaponInfo if weapon['WeaponName'] in infoText
            }

            self.saveJson(items, 'items.json')
            self.saveJson(weapons, 'weapons.json')
            logger.info(f'Generated items.json ({len(items)} items) and weapons.json ({len(weapons)} weapons)')

        except Exception as e:
            logger.error(f'Failed to generate items data: {e}', exc_info=True)

    def updateJsonFromPattern(self, fileName: str, pattern: str, 
                            transformFunc: Callable[[str, Any], Optional[str]]) -> Dict:
        """Generic method to extract data using regex patterns"""
        logger.info(f'Generating {fileName}...')
        try:
            infoText = self.loadJson('MultiText.json')
            
            if not infoText:
                logger.error('MultiText.json not found or empty')
                return {}

            data = {}
            compiledPattern = re.compile(pattern)
            
            for key in infoText:
                match = compiledPattern.match(key)
                if match:
                    transformed = transformFunc(infoText[key], match)
                    if transformed is not None:
                        data[transformed] = int(match.group(1))

            self.saveJson(data, fileName)
            logger.info(f'Generated {fileName} with {len(data)} entries')
            return data

        except Exception as e:
            logger.error(f'Failed to generate {fileName}: {e}', exc_info=True)
            return {}

    def updateCharacters(self):
        """Generate characters.json"""
        self.updateJsonFromPattern(
            'characters.json',
            r'^RoleInfo_(\d+)_Name$',
            lambda text, match: text.lower().replace(' ', '') if int(match.group(1)) < 5000 else None
        )

    def updateEcho(self):
        """Generate echoes.json"""
        self.updateJsonFromPattern(
            'echoes.json',
            r'^MonsterInfo_(\d+)_Name$',
            lambda text, match: text.lower().replace(' ', '') if int(match.group(1)) < 350000000 else None
        )

    def updateAchievements(self):
        """Generate achievements.json"""
        self.updateJsonFromPattern(
            'achievements.json',
            r'^Achievement_(\d+)_Name$',
            lambda text, _: text
        )

    def updateEchoStats(self):
        """Generate echoStats.json"""
        statsKey = {
            'PropertyIndex_10003_Name': 'hp',
            'PropertyIndex_10007_Name': 'atk',
            'PropertyIndex_10008_Name': 'cr',
            'PropertyIndex_10009_Name': 'cd',
            'PropertyIndex_10010_Name': 'def',
            'PropertyIndex_10011_Name': 'er',
            'PropertyIndex_10014_Name': 'skillDmg',
            'PropertyIndex_10017_Name': 'basicAttack',
            'PropertyIndex_10018_Name': 'heavyAttack',
            'PropertyIndex_10019_Name': 'liberationDmg',
            'PropertyIndex_10022_Name': 'glacio',
            'PropertyIndex_10023_Name': 'fusion',
            'PropertyIndex_10024_Name': 'electro',
            'PropertyIndex_10025_Name': 'aero',
            'PropertyIndex_10026_Name': 'spectro',
            'PropertyIndex_10027_Name': 'havoc',
            'PropertyIndex_10035_Name': 'healing'
        }

        logger.info('Generating echoStats.json...')
        try:
            infoText = self.loadJson('MultiText.json')
            
            if not infoText:
                logger.error('MultiText.json not found or empty')
                return

            stats = {
                infoText[key].lower().replace(' ', '').replace('.', ''): value
                for key, value in statsKey.items() if key in infoText
            }
            
            self.saveJson(stats, 'echoStats.json')
            logger.info(f'Generated echoStats.json with {len(stats)} entries')
            
        except Exception as e:
            logger.error(f'Failed to generate echoStats.json: {e}', exc_info=True)

    def updateSonata(self):
        """Generate sonataName.json"""
        data = self.updateJsonFromPattern(
            'sonataName.json',
            r'^PhantomFetter_(\d+)_Name$',
            lambda text, _: text.lower().replace(' ', '')
        )

    def updateDefinedText(self):
        """Generate definedText.json"""
        textKey = [
            'PrefabTextItem_1547656443_Text',  # Terminal
            'PrefabTextItem_128820487_Text',   # Claim
            'PrefabTextItem_3963945691_Text'   # Activated
        ]

        logger.info('Generating definedText.json...')
        try:
            infoText = self.loadJson('MultiText.json')
            
            if not infoText:
                logger.error('MultiText.json not found or empty')
                return

            stats = {
                key: infoText[key].lower().replace(' ', '').replace('-', '').strip()
                for key in textKey if key in infoText
            }
            
            self.saveJson(stats, 'definedText.json')
            logger.info(f'Generated definedText.json')
            
        except Exception as e:
            logger.error(f'Failed to generate definedText.json: {e}', exc_info=True)

    def run(self):
        """Execute the full update process"""
        logger.info('Starting data update...')
        logger.info(f'Using language: {self.lang}')
        
        self.updateFiles()
        
        if self.updated:
            logger.info('Files were updated, regenerating derived files...')
            self.updateItems()
            self.updateEchoStats()
            self.updateSonata()
            self.updateDefinedText()
            self.updateAchievements()
            self.updateCharacters()
            self.updateEcho()
        else:
            logger.info('All files are up to date')
        
        logger.info('Update process completed')


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Update WuWa game data files'
    )
    parser.add_argument(
        '-l', '--lang',
        type=str,
        default=None,
        help='Language code (e.g., en, zh-Hans, ja, ko). Leave empty to auto-detect.'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logging.getLogger('').setLevel(logging.DEBUG)
    
    updater = CLIDataUpdater(lang=args.lang)
    updater.run()


if __name__ == '__main__':
    main()
