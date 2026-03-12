"""Dependency extractor for indexing package dependencies."""

from pathlib import Path
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Set, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from src.database.models import Dependency, Repository
from src.parsers.npm_parser import NpmParser, NpmPackage
from src.parsers.python_dependency_parser import PythonDependencyParser, PythonPackage
from src.extractors.strategy_interfaces import DependencyManifestStrategy
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class JavaPackage:
    """Represents a Java dependency (Maven/Gradle)."""
    package_name: str
    version: Optional[str] = None
    version_constraint: Optional[str] = None
    is_dev_dependency: bool = False
    is_transitive: bool = False
    file_path: str = ""
    dependency_type: str = "maven"


class NpmDependencyStrategy:
    """Dependency strategy for npm manifests."""

    dependency_type = "npm"

    def __init__(self, npm_parser: NpmParser):
        self.npm_parser = npm_parser

    def supports(self, file_name: str) -> bool:
        return file_name in {'package.json', 'package-lock.json'}

    def parse_file(self, file_path: Path) -> List[NpmPackage]:
        # Preserve previous duplicate-avoidance behavior.
        if file_path.name.lower() == 'package.json':
            lock_file = file_path.parent / 'package-lock.json'
            if lock_file.exists():
                return []
        return self.npm_parser.parse_file(file_path)


class PythonDependencyStrategy:
    """Dependency strategy for Python manifests."""

    dependency_type = "pip"

    def __init__(self, python_parser: PythonDependencyParser):
        self.python_parser = python_parser

    def supports(self, file_name: str) -> bool:
        return file_name in {'requirements.txt', 'pyproject.toml', 'pipfile'} or file_name.endswith('-requirements.txt')

    def parse_file(self, file_path: Path) -> List[PythonPackage]:
        return self.python_parser.parse_file(file_path)


class MavenDependencyStrategy:
    """Dependency strategy for Maven manifests."""

    dependency_type = "maven"

    def __init__(self, parser_fn):
        self._parser_fn = parser_fn

    def supports(self, file_name: str) -> bool:
        return file_name == 'pom.xml'

    def parse_file(self, file_path: Path) -> List[JavaPackage]:
        return self._parser_fn(file_path)


class GradleDependencyStrategy:
    """Dependency strategy for Gradle manifests."""

    dependency_type = "gradle"

    def __init__(self, parser_fn):
        self._parser_fn = parser_fn

    def supports(self, file_name: str) -> bool:
        return file_name in {'build.gradle', 'build.gradle.kts'}

    def parse_file(self, file_path: Path) -> List[JavaPackage]:
        return self._parser_fn(file_path)


class DependencyExtractor:
    """Extracts and indexes package dependencies from various package managers."""
    
    # Dependency file patterns to search for
    DEPENDENCY_FILES = {
        'npm': ['package.json', 'package-lock.json'],
        'python': ['requirements.txt', 'pyproject.toml', 'Pipfile'],
        'java': ['pom.xml', 'build.gradle', 'build.gradle.kts'],
    }
    
    def __init__(self, session: AsyncSession):
        """
        Initialize dependency extractor.
        
        Args:
            session: Database session
        """
        self.session = session
        self.npm_parser = NpmParser()
        self.python_parser = PythonDependencyParser()
        self.dependency_strategies: List[DependencyManifestStrategy] = [
            NpmDependencyStrategy(self.npm_parser),
            PythonDependencyStrategy(self.python_parser),
            MavenDependencyStrategy(self._parse_maven_pom),
            GradleDependencyStrategy(self._parse_gradle_build_file),
        ]
    
    async def extract_dependencies(self, repository_id: int, repo_path: Path) -> int:
        """
        Extract all dependencies from a repository.
        
        Args:
            repository_id: Repository ID
            repo_path: Path to repository root
            
        Returns:
            Number of dependencies found
        """
        logger.info(
            "dependency_extraction_started",
            repository_id=repository_id,
            repo_path=str(repo_path)
        )
        
        # Clear existing dependencies for this repository
        await self._clear_existing_dependencies(repository_id)
        
        # Detect all dependency files
        dependency_files = self._detect_dependency_files(repo_path)
        
        logger.debug(
            "dependency_files_detected",
            repository_id=repository_id,
            files_found=len(dependency_files)
        )
        
        # Parse and store dependencies
        total_dependencies = 0
        for file_path in dependency_files:
            count = await self._parse_and_store(file_path, repository_id, repo_path)
            total_dependencies += count
        
        # Commit all changes
        await self.session.commit()
        
        logger.info(
            "dependency_extraction_completed",
            repository_id=repository_id,
            dependencies_found=total_dependencies,
            files_processed=len(dependency_files)
        )
        
        return total_dependencies
    
    def _detect_dependency_files(self, repo_path: Path) -> List[Path]:
        """
        Detect all dependency files in the repository.
        
        Args:
            repo_path: Path to repository root
            
        Returns:
            List of dependency file paths
        """
        dependency_files = []
        
        # Walk through repository
        for root, dirs, files in os.walk(repo_path):
            root_path = Path(root)
            # Skip common directories to ignore
            dirs[:] = [d for d in dirs if d not in {
                'node_modules', '.git', '.venv', 'venv', 'env',
                '__pycache__', 'bin', 'obj', 'dist', 'build',
                '.pytest_cache', '.mypy_cache', '.tox'
            }]
            
            for file in files:
                file_path = root_path / file
                file_name = file.lower()
                
                # Check if it's a dependency file
                if self._is_dependency_file(file_name):
                    dependency_files.append(file_path)
        
        return dependency_files
    
    def _is_dependency_file(self, file_name: str) -> bool:
        """
        Check if a file is a dependency file.
        
        Args:
            file_name: File name (lowercase)
            
        Returns:
            True if it's a dependency file
        """
        # npm files
        if file_name in {'package.json', 'package-lock.json'}:
            return True
        
        # Python files
        if file_name in {'requirements.txt', 'pyproject.toml', 'pipfile'} or \
           file_name.endswith('-requirements.txt'):
            return True

        # Java files
        if file_name in {'pom.xml', 'build.gradle', 'build.gradle.kts'}:
            return True
        
        return False
    
    async def _parse_and_store(
        self,
        file_path: Path,
        repository_id: int,
        repo_path: Path
    ) -> int:
        """
        Parse a dependency file and store results in database.
        
        Args:
            file_path: Path to dependency file
            repository_id: Repository ID
            repo_path: Repository root path
            
        Returns:
            Number of dependencies stored
        """
        try:
            # Determine parser based on file type
            file_name = file_path.name.lower()
            packages = []
            strategy = next((s for s in self.dependency_strategies if s.supports(file_name)), None)
            if strategy:
                packages = strategy.parse_file(file_path)
            
            # Store packages in database
            if packages:
                # Get relative path from repo root
                try:
                    relative_path = file_path.relative_to(repo_path)
                except ValueError:
                    relative_path = file_path
                
                for package in packages:
                    dependency = Dependency(
                        repository_id=repository_id,
                        package_name=package.package_name,
                        package_version=package.version,
                        version_constraint=package.version_constraint,
                        dependency_type=package.dependency_type,
                        is_dev_dependency=1 if package.is_dev_dependency else 0,
                        is_transitive=1 if getattr(package, 'is_transitive', False) else 0,
                        file_path=str(relative_path)
                    )
                    self.session.add(dependency)
                
                logger.debug(
                    "dependencies_stored",
                    file_path=str(file_path),
                    packages_stored=len(packages)
                )
                
                return len(packages)
            
        except Exception as e:
            logger.error(
                "error_parsing_dependency_file",
                file_path=str(file_path),
                error=str(e)
            )
        
        return 0

    def _parse_maven_pom(self, file_path: Path) -> List[JavaPackage]:
        """Parse Maven `pom.xml` dependencies."""
        packages: List[JavaPackage] = []
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()

            # Handle optional XML namespace.
            ns_match = re.match(r"\{(.+)\}", root.tag)
            ns = {"m": ns_match.group(1)} if ns_match else {}
            prefix = "m:" if ns else ""

            dependency_nodes = root.findall(f".//{prefix}dependency", ns)
            for dep in dependency_nodes:
                group_id = dep.findtext(f"{prefix}groupId", default="", namespaces=ns).strip()
                artifact_id = dep.findtext(f"{prefix}artifactId", default="", namespaces=ns).strip()
                version = dep.findtext(f"{prefix}version", default="", namespaces=ns).strip()
                scope = dep.findtext(f"{prefix}scope", default="", namespaces=ns).strip().lower()

                if not group_id or not artifact_id:
                    continue

                packages.append(
                    JavaPackage(
                        package_name=f"{group_id}:{artifact_id}",
                        version=version or None,
                        version_constraint=version or None,
                        is_dev_dependency=scope in {"test", "provided"},
                        is_transitive=False,
                        file_path=str(file_path),
                        dependency_type="maven",
                    )
                )

            logger.debug("parsed_maven_pom", file_path=str(file_path), packages_found=len(packages))
        except Exception as e:
            logger.error("error_parsing_maven_pom", file_path=str(file_path), error=str(e))
        return packages

    def _parse_gradle_build_file(self, file_path: Path) -> List[JavaPackage]:
        """Parse Gradle dependencies from `build.gradle` or `build.gradle.kts`."""
        packages: List[JavaPackage] = []
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")

            # Matches:
            # implementation 'group:artifact:version'
            # testImplementation("group:artifact:version")
            # api("group:artifact")
            pattern = re.compile(
                r"\b(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|annotationProcessor|kapt)\b"
                r"\s*(?:\(\s*)?[\"']([^\"']+)[\"']"
            )
            for match in pattern.finditer(text):
                config = match.group(1)
                notation = match.group(2).strip()

                parts = notation.split(":")
                if len(parts) < 2:
                    continue

                group_id = parts[0].strip()
                artifact_id = parts[1].strip()
                version = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
                if not group_id or not artifact_id:
                    continue

                packages.append(
                    JavaPackage(
                        package_name=f"{group_id}:{artifact_id}",
                        version=version,
                        version_constraint=version,
                        is_dev_dependency=config.lower().startswith("test"),
                        is_transitive=False,
                        file_path=str(file_path),
                        dependency_type="gradle",
                    )
                )

            logger.debug("parsed_gradle_build_file", file_path=str(file_path), packages_found=len(packages))
        except Exception as e:
            logger.error("error_parsing_gradle_build_file", file_path=str(file_path), error=str(e))
        return packages
    
    async def _clear_existing_dependencies(self, repository_id: int):
        """
        Clear existing dependencies for a repository.
        
        Args:
            repository_id: Repository ID
        """
        try:
            await self.session.execute(
                delete(Dependency).where(Dependency.repository_id == repository_id)
            )
            logger.debug(
                "cleared_existing_dependencies",
                repository_id=repository_id
            )
        except Exception as e:
            logger.error(
                "error_clearing_dependencies",
                repository_id=repository_id,
                error=str(e)
            )
