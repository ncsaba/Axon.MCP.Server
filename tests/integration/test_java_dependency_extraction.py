from pathlib import Path

import pytest
from sqlalchemy import select

from src.config.enums import RepositoryStatusEnum, SourceControlProviderEnum
from src.database.models import Dependency, Repository
from src.extractors.dependency_extractor import DependencyExtractor


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_java_dependency_manifests_are_extracted(async_session, tmp_path: Path):
    """Extract dependencies from `pom.xml` and `build.gradle`.

    Temporary validation test for the Java dependency vertical slice.
    Remove after the Java semantic extractor set is considered stable.
    """
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999005,
        name="java-dependency-repo",
        path_with_namespace="integration/java-dependency-repo",
        url="https://example.com/integration/java-dependency-repo.git",
        clone_url="https://example.com/integration/java-dependency-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    (tmp_path / "pom.xml").write_text(
        "\n".join(
            [
                "<project>",
                "  <dependencies>",
                "    <dependency>",
                "      <groupId>org.springframework.boot</groupId>",
                "      <artifactId>spring-boot-starter-web</artifactId>",
                "      <version>3.2.0</version>",
                "    </dependency>",
                "    <dependency>",
                "      <groupId>org.junit.jupiter</groupId>",
                "      <artifactId>junit-jupiter</artifactId>",
                "      <version>5.10.0</version>",
                "      <scope>test</scope>",
                "    </dependency>",
                "  </dependencies>",
                "</project>",
            ]
        ),
        encoding="utf-8",
    )

    (tmp_path / "build.gradle").write_text(
        "\n".join(
            [
                "dependencies {",
                "  implementation 'com.google.guava:guava:33.0.0-jre'",
                "  testImplementation \"org.mockito:mockito-core:5.11.0\"",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    extractor = DependencyExtractor(async_session)
    found = await extractor.extract_dependencies(repo.id, tmp_path)
    assert found >= 4

    result = await async_session.execute(
        select(Dependency).where(Dependency.repository_id == repo.id)
    )
    deps = result.scalars().all()
    by_name = {dep.package_name: dep for dep in deps}

    assert "org.springframework.boot:spring-boot-starter-web" in by_name
    assert by_name["org.springframework.boot:spring-boot-starter-web"].dependency_type == "maven"

    assert "org.junit.jupiter:junit-jupiter" in by_name
    assert by_name["org.junit.jupiter:junit-jupiter"].is_dev_dependency == 1

    assert "com.google.guava:guava" in by_name
    assert by_name["com.google.guava:guava"].dependency_type == "gradle"

    assert "org.mockito:mockito-core" in by_name
    assert by_name["org.mockito:mockito-core"].is_dev_dependency == 1


@pytest.mark.asyncio
async def test_java_dependency_manifests_support_bom_properties_and_catalogs(async_session, tmp_path: Path):
    """Extract Maven property/BOM-managed and Gradle catalog/variable dependencies."""
    repo = Repository(
        provider=SourceControlProviderEnum.GITLAB,
        gitlab_project_id=999108,
        name="java-dependency-parity-repo",
        path_with_namespace="integration/java-dependency-parity-repo",
        url="https://example.com/integration/java-dependency-parity-repo.git",
        clone_url="https://example.com/integration/java-dependency-parity-repo.git",
        default_branch="main",
        status=RepositoryStatusEnum.PENDING,
    )
    async_session.add(repo)
    await async_session.flush()

    (tmp_path / "pom.xml").write_text(
        "\n".join(
            [
                "<project>",
                "  <modelVersion>4.0.0</modelVersion>",
                "  <parent>",
                "    <groupId>org.springframework.boot</groupId>",
                "    <artifactId>spring-boot-starter-parent</artifactId>",
                "    <version>3.2.2</version>",
                "  </parent>",
                "  <properties>",
                "    <junit.version>5.10.2</junit.version>",
                "  </properties>",
                "  <dependencyManagement>",
                "    <dependencies>",
                "      <dependency>",
                "        <groupId>com.fasterxml.jackson.core</groupId>",
                "        <artifactId>jackson-databind</artifactId>",
                "        <version>2.17.0</version>",
                "      </dependency>",
                "      <dependency>",
                "        <groupId>org.springframework.boot</groupId>",
                "        <artifactId>spring-boot-dependencies</artifactId>",
                "        <version>${project.version}</version>",
                "        <type>pom</type>",
                "        <scope>import</scope>",
                "      </dependency>",
                "    </dependencies>",
                "  </dependencyManagement>",
                "  <dependencies>",
                "    <dependency>",
                "      <groupId>org.junit.jupiter</groupId>",
                "      <artifactId>junit-jupiter</artifactId>",
                "      <version>${junit.version}</version>",
                "      <scope>test</scope>",
                "    </dependency>",
                "    <dependency>",
                "      <groupId>com.fasterxml.jackson.core</groupId>",
                "      <artifactId>jackson-databind</artifactId>",
                "    </dependency>",
                "  </dependencies>",
                "</project>",
            ]
        ),
        encoding="utf-8",
    )

    (tmp_path / "gradle").mkdir(parents=True, exist_ok=True)
    (tmp_path / "gradle" / "libs.versions.toml").write_text(
        "\n".join(
            [
                "[versions]",
                "junit = \"5.10.2\"",
                "slf4j = \"2.0.13\"",
                "",
                "[libraries]",
                "junit-jupiter = { module = \"org.junit.jupiter:junit-jupiter\", version.ref = \"junit\" }",
                "slf4j-api = { group = \"org.slf4j\", name = \"slf4j-api\", version.ref = \"slf4j\" }",
                "spring-boot-bom = { module = \"org.springframework.boot:spring-boot-dependencies\", version = \"3.2.2\" }",
            ]
        ),
        encoding="utf-8",
    )

    (tmp_path / "build.gradle").write_text(
        "\n".join(
            [
                "ext {",
                "  guavaVersion = \"33.2.1-jre\"",
                "}",
                "dependencies {",
                "  implementation \"com.google.guava:guava:$guavaVersion\"",
                "  implementation(platform(libs.spring.boot.bom))",
                "  implementation(libs.slf4j.api)",
                "  testImplementation(libs.junit.jupiter)",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    extractor = DependencyExtractor(async_session)
    found = await extractor.extract_dependencies(repo.id, tmp_path)
    assert found >= 6

    result = await async_session.execute(
        select(Dependency).where(Dependency.repository_id == repo.id)
    )
    deps = result.scalars().all()
    by_name = {dep.package_name: dep for dep in deps}

    assert "org.junit.jupiter:junit-jupiter" in by_name
    assert by_name["org.junit.jupiter:junit-jupiter"].package_version == "5.10.2"
    assert by_name["org.junit.jupiter:junit-jupiter"].is_dev_dependency == 1

    assert "com.fasterxml.jackson.core:jackson-databind" in by_name
    assert by_name["com.fasterxml.jackson.core:jackson-databind"].package_version == "2.17.0"
    assert by_name["com.fasterxml.jackson.core:jackson-databind"].dependency_type == "maven"

    assert "com.google.guava:guava" in by_name
    assert by_name["com.google.guava:guava"].package_version == "33.2.1-jre"

    assert "org.slf4j:slf4j-api" in by_name
    assert by_name["org.slf4j:slf4j-api"].package_version == "2.0.13"

    assert "org.springframework.boot:spring-boot-dependencies" in by_name
    assert by_name["org.springframework.boot:spring-boot-dependencies"].dependency_type == "gradle"
