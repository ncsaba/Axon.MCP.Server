"""API endpoint extractor for Web APIs."""

import re
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Symbol, File, Repository
from src.config.enums import SymbolKindEnum, LanguageEnum, AccessModifierEnum, SourceControlProviderEnum
from src.config.settings import get_settings
from src.gitlab.repository_manager import RepositoryManager
from src.azuredevops.repository_manager import AzureDevOpsRepositoryManager
from src.extractors.strategy_interfaces import EndpointExtractionStrategy


class ApiEndpoint:
    """Represents an API endpoint."""
    
    def __init__(
        self,
        http_method: str,
        route: str,
        controller: str,
        action: str,
        file_path: str,
        file_id: int,
        language: LanguageEnum,
        line_number: int,
        requires_auth: bool = False,
        parameters: Optional[List[Dict]] = None,
    ):
        self.http_method = http_method
        self.route = route
        self.controller = controller
        self.action = action
        self.file_path = file_path
        self.file_id = file_id
        self.language = language
        self.line_number = line_number
        self.requires_auth = requires_auth
        self.parameters = parameters or []


class SymbolBasedEndpointStrategy:
    """Endpoint strategy using persisted symbols/attributes."""

    def __init__(self, extractor: "ApiEndpointExtractor"):
        self.extractor = extractor

    async def extract_endpoints(self, repository_id: int) -> List[ApiEndpoint]:
        return await self.extractor._extract_symbol_based_endpoints(repository_id)


class JavaAnnotationEndpointStrategy:
    """Endpoint strategy using Java source annotations."""

    def __init__(self, extractor: "ApiEndpointExtractor"):
        self.extractor = extractor

    async def extract_endpoints(self, repository_id: int) -> List[ApiEndpoint]:
        return await self.extractor._extract_java_annotation_endpoints(repository_id)


class ApiEndpointExtractor:
    """Extracts API endpoints from parsed code."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.endpoint_strategies: List[EndpointExtractionStrategy] = [
            SymbolBasedEndpointStrategy(self),
            JavaAnnotationEndpointStrategy(self),
        ]
    
    async def extract_endpoints(
        self,
        repository_id: int
    ) -> List[ApiEndpoint]:
        """
        Extract all API endpoints from a repository.
        
        Args:
            repository_id: Repository ID
            
        Returns:
            List of API endpoints
        """
        from src.utils.logging_config import get_logger
        logger = get_logger(__name__)
        
        endpoints: List[ApiEndpoint] = []
        for strategy in self.endpoint_strategies:
            try:
                strategy_endpoints = await strategy.extract_endpoints(repository_id)
                endpoints.extend(strategy_endpoints)
            except Exception as e:
                logger.error(
                    "api_endpoint_strategy_failed",
                    strategy=strategy.__class__.__name__,
                    repository_id=repository_id,
                    error=str(e),
                )

        logger.info(
            "api_extractor_total_endpoints",
            repository_id=repository_id,
            total=len(endpoints),
        )
        return endpoints

    async def _extract_symbol_based_endpoints(self, repository_id: int) -> List[ApiEndpoint]:
        """Extract endpoints from existing symbol metadata and generated endpoints."""
        from src.utils.logging_config import get_logger
        logger = get_logger(__name__)

        endpoints: List[ApiEndpoint] = []

        result = await self.session.execute(
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                Symbol.kind == SymbolKindEnum.CLASS
            )
        )

        all_classes = result.all()
        logger.info("api_extractor_found_classes", count=len(all_classes), repository_id=repository_id)

        controller_count = 0
        for class_symbol, file in all_classes:
            if not self._is_controller(class_symbol):
                continue

            controller_count += 1
            class_route = self._extract_route_from_attributes(class_symbol)

            methods_result = await self.session.execute(
                select(Symbol)
                .where(
                    Symbol.file_id == class_symbol.file_id,
                    Symbol.kind == SymbolKindEnum.METHOD,
                    Symbol.parent_name == class_symbol.fully_qualified_name
                )
            )
            methods = methods_result.scalars().all()

            for method in methods:
                endpoint = self._extract_endpoint_from_method(
                    method,
                    class_symbol,
                    class_route,
                    file.path,
                    file.id,
                    file.language
                )
                if endpoint:
                    endpoints.append(endpoint)

        logger.info(
            "api_extractor_controllers_processed",
            repository_id=repository_id,
            controller_count=controller_count,
            endpoints_from_controllers=len(endpoints)
        )

        endpoints_result = await self.session.execute(
            select(Symbol, File)
            .join(File, Symbol.file_id == File.id)
            .where(
                File.repository_id == repository_id,
                Symbol.kind == SymbolKindEnum.ENDPOINT
            )
        )

        minimal_api_symbols = endpoints_result.all()
        logger.info("api_extractor_minimal_apis_found", repository_id=repository_id, count=len(minimal_api_symbols))

        for symbol, file in minimal_api_symbols:
            parts = symbol.name.split(' ', 1)
            if len(parts) == 2:
                http_method, route = parts
            else:
                http_method = "UNKNOWN"
                route = symbol.name

            docs = symbol.structured_docs or {}
            endpoint = ApiEndpoint(
                http_method=docs.get('method', http_method),
                route=docs.get('path', route),
                controller=docs.get('type', 'MinimalApi'),
                action=symbol.name,
                file_path=file.path,
                file_id=file.id,
                language=file.language,
                line_number=symbol.start_line,
                requires_auth=False,
                parameters=[]
            )
            endpoints.append(endpoint)

        return endpoints

    async def save_endpoints(self, endpoints: List[ApiEndpoint]) -> int:
        """
        Save extracted endpoints as Symbol records.
        
        Args:
            endpoints: List of ApiEndpoint objects
            
        Returns:
            Count of saved endpoints
        """
        count = 0
        for endpoint in endpoints:
            # Create a unique name for the endpoint symbol
            # e.g., "GET /api/users/{id}"
            name = f"{endpoint.http_method} {endpoint.route}"
            
            # Create Symbol
            symbol = Symbol(
                file_id=endpoint.file_id,
                language=endpoint.language,
                kind=SymbolKindEnum.ENDPOINT,
                access_modifier=AccessModifierEnum.PUBLIC,
                name=name,
                fully_qualified_name=f"{endpoint.controller}.{endpoint.action}:{endpoint.http_method}",
                start_line=endpoint.line_number,
                end_line=endpoint.line_number, # Approximate
                signature=f"{endpoint.http_method} {endpoint.route}",
                documentation=f"API Endpoint: {endpoint.http_method} {endpoint.route}\nController: {endpoint.controller}\nAction: {endpoint.action}",
                structured_docs={
                    "type": "api_endpoint",
                    "http_method": endpoint.http_method,
                    "route": endpoint.route,
                    "controller": endpoint.controller,
                    "action": endpoint.action,
                    "requires_auth": endpoint.requires_auth,
                    "parameters": endpoint.parameters
                },
                is_generated=1 # Mark as generated since it's derived
            )
            
            self.session.add(symbol)
            count += 1
            
        return count
    
    def _is_controller(self, class_symbol: Symbol) -> bool:
        """Check if a class is a controller."""
        from src.utils.logging_config import get_logger
        logger = get_logger(__name__)
        
        # Check by name
        name_check = class_symbol.name.endswith('Controller')
        
        # Check by attributes
        attribute_check = False
        has_structured_docs = class_symbol.structured_docs is not None
        has_attributes = False
        attribute_names = []
        
        if has_structured_docs and 'attributes' in class_symbol.structured_docs:
            has_attributes = True
            attrs = class_symbol.structured_docs['attributes']
            attribute_names = [attr.get('name') for attr in attrs]
            attribute_check = any(name in ['ApiController', 'Controller'] for name in attribute_names)
        
        is_controller = name_check or attribute_check
        
        # Log details for first few classes to understand the pattern
        logger.debug(f"_is_controller_check",
                    class_name=class_symbol.name,
                    name_check=name_check,
                    has_structured_docs=has_structured_docs,
                    has_attributes=has_attributes,
                    attribute_names=attribute_names,
                    attribute_check=attribute_check,
                    is_controller=is_controller)
        
        return is_controller
    
    def _extract_route_from_attributes(self, symbol: Symbol) -> str:
        """Extract route from symbol attributes."""
        if not symbol.structured_docs or 'attributes' not in symbol.structured_docs:
            return ""
        
        attrs = symbol.structured_docs['attributes']
        route = ""
        area_name = ""
        
        # First pass: find Area attribute
        for attr in attrs:
            if attr.get('name') == 'Area':
                args = attr.get('arguments', {})
                # Handle structured arguments (dict) or legacy (list)
                if isinstance(args, dict):
                    positional = args.get('positional', [])
                    if positional:
                        area_name = str(positional[0]).strip('"\'')
                elif isinstance(args, list) and args:
                    area_name = args[0].strip('"\'')
        
        # Second pass: find Route attribute
        for attr in attrs:
            if attr.get('name') == 'Route':
                # Get the route template
                args = attr.get('arguments', {})
                current_route = ""
                
                if isinstance(args, dict):
                    # Check named 'Template' argument
                    if 'Template' in args.get('named', {}):
                        current_route = str(args['named']['Template']).strip('"\'')
                    # Check positional
                    elif args.get('positional'):
                        current_route = str(args['positional'][0]).strip('"\'')
                elif isinstance(args, list) and args:
                    current_route = args[0].strip('"\'')
                
                if current_route:
                    route = current_route
                    
                    # Handle [controller] placeholder
                    if '[controller]' in route:
                        controller_name = symbol.name.replace('Controller', '')
                        route = route.replace('[controller]', controller_name)
                        
                    # Handle [area] placeholder
                    if '[area]' in route and area_name:
                        route = route.replace('[area]', area_name)
                        
                    return route
        
        return ""
    
    def _extract_endpoint_from_method(
        self,
        method: Symbol,
        controller: Symbol,
        class_route: str,
        file_path: str,
        file_id: int,
        language: LanguageEnum
    ) -> Optional[ApiEndpoint]:
        """Extract API endpoint from a method."""
        if not method.structured_docs or 'attributes' not in method.structured_docs:
            return None
        
        attrs = method.structured_docs['attributes']
        http_method = None
        method_route = ""
        requires_auth = False
        
        # Check for HTTP method attributes
        for attr in attrs:
            attr_name = attr.get('name', '')
            
            # HTTP method attributes
            if attr_name == 'HttpGet':
                http_method = 'GET'
                method_route = self._get_route_from_attribute(attr)
            elif attr_name == 'HttpPost':
                http_method = 'POST'
                method_route = self._get_route_from_attribute(attr)
            elif attr_name == 'HttpPut':
                http_method = 'PUT'
                method_route = self._get_route_from_attribute(attr)
            elif attr_name == 'HttpDelete':
                http_method = 'DELETE'
                method_route = self._get_route_from_attribute(attr)
            elif attr_name == 'HttpPatch':
                http_method = 'PATCH'
                method_route = self._get_route_from_attribute(attr)
            elif attr_name in ['Route']:
                # Only use Route attribute if HTTP method is already found or implied?
                # Usually Route is used with HttpMethod, or on its own (implies GET? No).
                # But if we found HttpMethod, we might have already set method_route.
                # If we haven't found HttpMethod, Route doesn't define it.
                # But sometimes [Route("...")] is used on method.
                # Let's assume if we find Route on method, we might need to infer method or wait for HttpVerb.
                # For now, just extract route.
                r = self._get_route_from_attribute(attr)
                if r:
                    method_route = r
            
            # Authorization
            if attr_name in ['Authorize', 'Authenticated']:
                requires_auth = True
        
        if not http_method:
            return None
        
        # Combine routes
        full_route = self._combine_routes(class_route, method_route)
        
        # Extract parameters
        parameters = []
        if method.parameters:
            for param in method.parameters:
                if isinstance(param, dict):
                    parameters.append(param)
        
        return ApiEndpoint(
            http_method=http_method,
            route=full_route,
            controller=controller.name,
            action=method.name,
            file_path=file_path,
            file_id=file_id,
            language=language,
            line_number=method.start_line,
            requires_auth=requires_auth,
            parameters=parameters
        )
    
    def _get_route_from_attribute(self, attr: Dict) -> str:
        """Extract route string from attribute."""
        args = attr.get('arguments', {})
        if isinstance(args, dict):
            # Check named 'Template' argument
            if 'Template' in args.get('named', {}):
                return str(args['named']['Template']).strip('"\'')
            # Check positional
            elif args.get('positional'):
                return str(args['positional'][0]).strip('"\'')
        elif isinstance(args, list) and args:
            # Remove quotes
            return args[0].strip('"\'')
        return ""
    
    def _combine_routes(self, base_route: str, method_route: str) -> str:
        """Combine base and method routes."""
        if not base_route:
            base_route = ""
        if not method_route:
            method_route = ""
        
        # Ensure proper slashes
        if base_route and not base_route.startswith('/'):
            base_route = '/' + base_route
        
        if method_route and not method_route.startswith('/'):
            method_route = '/' + method_route
        
        # Combine
        full_route = base_route + method_route
        
        # Clean up double slashes
        while '//' in full_route:
            full_route = full_route.replace('//', '/')
        
        return full_route if full_route else '/'

    async def _extract_java_annotation_endpoints(self, repository_id: int) -> List[ApiEndpoint]:
        """Extract Java endpoints from source annotations (Spring/JAX-RS)."""
        result = await self.session.execute(
            select(Repository).where(Repository.id == repository_id)
        )
        repo = result.scalar_one_or_none()
        if not repo:
            return []

        repo_path = self._resolve_repository_path(repo)
        if not repo_path:
            return []

        files_result = await self.session.execute(
            select(File).where(
                File.repository_id == repository_id,
                File.language == LanguageEnum.JAVA,
            )
        )
        java_files = files_result.scalars().all()

        endpoints: List[ApiEndpoint] = []
        for file in java_files:
            file_path = repo_path / file.path
            if not file_path.exists():
                continue
            try:
                code = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            endpoints.extend(self._extract_java_endpoints_from_code(code, file))
        return endpoints

    def _resolve_repository_path(self, repo: Repository) -> Optional[Path]:
        """Resolve repository disk path from provider metadata."""
        if repo.provider == SourceControlProviderEnum.AZUREDEVOPS:
            if not repo.azuredevops_project_name:
                return None
            repo_manager = AzureDevOpsRepositoryManager(get_settings().repo_cache_dir)
            return repo_manager.get_repository_path(repo.azuredevops_project_name, repo.name)

        repo_manager = RepositoryManager(get_settings().repo_cache_dir)
        return repo_manager.cache_dir / repo.path_with_namespace.replace("/", "_")

    def _extract_java_endpoints_from_code(self, code: str, file: File) -> List[ApiEndpoint]:
        """Extract endpoints from Java source code annotations."""
        endpoints: List[ApiEndpoint] = []
        lines = code.splitlines()

        class_name: Optional[str] = None
        class_route = ""
        class_is_controller = False
        pending_annotations: List[str] = []

        for idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("@"):
                pending_annotations.append(line)
                continue

            class_match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if class_match:
                class_name = class_match.group(1)
                class_route = self._extract_base_route_from_java_annotations(pending_annotations)
                class_is_controller = self._is_java_controller(class_name, pending_annotations)
                pending_annotations = []
                continue

            method_match = re.search(
                r"(?:public|protected|private)?\s*(?:static\s+)?[A-Za-z0-9_<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                line,
            )
            if method_match and class_name:
                if class_is_controller:
                    method_name = method_match.group(1)
                    endpoint_info = self._extract_java_method_endpoint_info(pending_annotations)
                    if endpoint_info:
                        http_method, method_route = endpoint_info
                        full_route = self._combine_routes(class_route, method_route)
                        endpoints.append(
                            ApiEndpoint(
                                http_method=http_method,
                                route=full_route,
                                controller=class_name,
                                action=method_name,
                                file_path=file.path,
                                file_id=file.id,
                                language=file.language,
                                line_number=idx,
                                requires_auth=False,
                                parameters=[],
                            )
                        )
                pending_annotations = []
                continue

            # Reset if we encountered non-annotation, non-signature content.
            if pending_annotations:
                pending_annotations = []

        return endpoints

    def _is_java_controller(self, class_name: str, annotations: List[str]) -> bool:
        """Determine whether a Java class is a web controller/resource."""
        if class_name.endswith("Controller"):
            return True
        controller_annotations = {"RestController", "Controller", "Path"}
        names = {self._java_annotation_name(a) for a in annotations}
        return bool(controller_annotations & names)

    def _extract_base_route_from_java_annotations(self, annotations: List[str]) -> str:
        """Extract class-level base route from Java annotations."""
        for annotation in annotations:
            name = self._java_annotation_name(annotation)
            if name in {"RequestMapping", "Path"}:
                route = self._java_annotation_route(annotation)
                if route:
                    return route
        return ""

    def _extract_java_method_endpoint_info(
        self, annotations: List[str]
    ) -> Optional[Tuple[str, str]]:
        """Extract (HTTP method, route) from Java method annotations."""
        mapping_map = {
            "GetMapping": "GET",
            "PostMapping": "POST",
            "PutMapping": "PUT",
            "DeleteMapping": "DELETE",
            "PatchMapping": "PATCH",
            "GET": "GET",
            "POST": "POST",
            "PUT": "PUT",
            "DELETE": "DELETE",
            "PATCH": "PATCH",
        }

        request_method = None
        route = ""
        for annotation in annotations:
            name = self._java_annotation_name(annotation)
            if name in mapping_map:
                request_method = mapping_map[name]
                route = self._java_annotation_route(annotation) or route
            elif name == "RequestMapping":
                route = self._java_annotation_route(annotation) or route
                method_match = re.search(
                    r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)", annotation
                )
                if method_match:
                    request_method = method_match.group(1)

        if not request_method:
            return None
        return request_method, route

    def _java_annotation_name(self, annotation: str) -> str:
        """Extract simple annotation name from Java annotation line."""
        cleaned = annotation.strip().lstrip("@")
        cleaned = cleaned.split("(", 1)[0]
        return cleaned.split(".")[-1]

    def _java_annotation_route(self, annotation: str) -> str:
        """Extract route path from Java annotation arguments."""
        named_match = re.search(r"(?:value|path)\s*=\s*\"([^\"]+)\"", annotation)
        if named_match:
            return named_match.group(1)
        positional_match = re.search(r"\"([^\"]+)\"", annotation)
        if positional_match:
            return positional_match.group(1)
        return ""
