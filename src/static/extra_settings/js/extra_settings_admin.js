(function() {
    'use strict';

    function updateValueFieldVisibility() {
        const typeSelect = document.querySelector('select[name="value_type"]');
        if (!typeSelect) {
            console.log('Extra Settings: Type select not found');
            return;
        }

        const selectedType = typeSelect.value;
        console.log('Extra Settings: Selected type:', selectedType);
        document.body.setAttribute('data-setting-type', selectedType);

        // Also log which field should be visible
        const visibleField = document.querySelector(`.field-value_${selectedType}`);
        if (visibleField) {
            console.log('Extra Settings: Field should be visible:', `.field-value_${selectedType}`);
        } else {
            console.log('Extra Settings: Field not found:', `.field-value_${selectedType}`);
        }
    }

    function initializeSettingForm() {
        console.log('Extra Settings: Initializing form');
        const typeSelect = document.querySelector('select[name="value_type"]');
        if (!typeSelect) {
            console.log('Extra Settings: Type select not found during init');
            return;
        }

        // Set initial state
        updateValueFieldVisibility();

        // Listen for changes
        typeSelect.addEventListener('change', function() {
            console.log('Extra Settings: Type changed');
            updateValueFieldVisibility();
        });

        // Disable type field if editing existing setting
        const nameField = document.querySelector('input[name="name"]');
        if (nameField && nameField.value) {
            // This is an edit, not a new setting
            console.log('Extra Settings: Editing existing setting, disabling type field');
            typeSelect.disabled = true;
            typeSelect.style.opacity = '0.6';
            typeSelect.style.cursor = 'not-allowed';

            // Add a note
            const helpText = document.createElement('div');
            helpText.className = 'help';
            helpText.style.marginTop = '0.5rem';
            helpText.style.fontSize = '0.875rem';
            helpText.style.color = '#6b7280';
            helpText.textContent = 'Type cannot be changed after creation';
            typeSelect.parentElement.appendChild(helpText);
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeSettingForm);
    } else {
        initializeSettingForm();
    }
})();
