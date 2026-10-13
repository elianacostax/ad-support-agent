from microsoft_teams.cards import (
    AdaptiveCard,
    ExecuteAction,
    Fact,
    FactSet,
    TextBlock,
)


def build_test_approval_card() -> AdaptiveCard:
    return AdaptiveCard(
        schema="http://adaptivecards.io/schemas/adaptive-card.json",
        version="1.5",
        body=[
            TextBlock(
                text="Solicitud de prueba",
                weight="Bolder",
                size="Large",
                wrap=True,
            ),
            TextBlock(
                text="Esta tarjeta verifica el flujo de aprobación "
                "directamente dentro de Microsoft Teams.",
                wrap=True,
            ),
            FactSet(
                facts=[
                    Fact(title="Solicitud", value="#TEST"),
                    Fact(title="Usuario AD", value="lvallejog"),
                    Fact(title="Acción", value="Desbloquear cuenta"),
                ]
            ),
        ],
        actions=[
            ExecuteAction(
                title="Aprobar",
                style="positive",
                data={
                    "action": "test_approve",
                },
            ),
            ExecuteAction(
                title="Rechazar",
                style="destructive",
                data={
                    "action": "test_reject",
                },
            ),
        ],
    )