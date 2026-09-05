from review_platform.main import create_app
from review_platform.settings import Settings


def test_delivery_routes_are_registered_with_exact_frozen_operations() -> None:
    app = create_app(Settings())
    operations = {
        (route.path, method): route.operation_id
        for route in app.routes
        if hasattr(route, "operation_id") and hasattr(route, "methods")
        for method in route.methods
    }
    assert operations[("/api/v1/deliveries", "GET")] == "listDeliveries"
    assert operations[("/api/v1/deliveries/{deliveryId}/retry", "POST")] == "retryDelivery"
