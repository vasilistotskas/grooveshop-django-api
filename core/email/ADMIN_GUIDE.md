# Email Template Management - Admin User Guide

## Quick Start

This guide will help you preview and test email templates through the Django admin interface.

## Accessing Email Template Management

1. **Log in to Django Admin**
   - Navigate to: `http://localhost:8000/admin/`
   - Enter your admin credentials

2. **Open Email Templates**
   - Look for the "Tools" section in the left sidebar
   - Click on "Email Templates" (📧 icon)

## Understanding the Interface

### Template List (Left Panel)

The left panel shows all available email templates organized by category:

**Search Box** (at the top):
- Type to search templates by name or description
- Press **Ctrl+K** (Windows/Linux) or **Cmd+K** (Mac) to focus search
- Results update in real-time
- Auto-expands collapsed categories with matches

**Categories** (collapsible):
- **Order Lifecycle**: Templates for order status changes (11 templates)
  - Order Confirmation, Shipped, Delivered, Canceled, etc.
- **Subscription**: Subscription-related templates
  - Subscription Confirmation
- **User Management**: User account templates
  - Inactive User Notification
- **Marketing**: Marketing and newsletter templates
  - Newsletter

**Category Features**:
- Click the arrow button (▼/▶) to collapse/expand each category
- Each category can be independently collapsed

Each template shows:
- **Status Badge**: 
  - 🟢 Active = Currently in use
  - ⚪ Unused = Available but not actively used
- **Format Icons**: HTML and/or TXT versions available
- **Description**: What the template is used for

### Preview Panel (Right Panel)

The preview panel allows you to test templates:

1. **Language Selector**
   - Greek (Ελληνικά)
   - English
   - German (Deutsch)

2. **Data Source Selector** (Smart Selection)
   - **Sample Data**: Uses generated test data (recommended for initial testing)
   - **Real Order**: Uses actual order from database (requires Order ID)
     - **Note**: Only available for order templates
     - Automatically hidden for subscription, user, and marketing templates

3. **Format Tabs**
   - **HTML**: Rich formatted email view
   - **Text**: Plain text email view

## How to Preview a Template

### Using Sample Data (Recommended)

1. Click on any template in the left panel (e.g., "Order Confirmation")
2. Select your preferred language from the dropdown
3. Keep "Sample Data" selected as the data source
4. Click "Generate Preview"
5. View the rendered email in the preview area
6. Switch between HTML and Text tabs to see both versions

### Using Real Order Data

1. Click on a template in the left panel
2. Select your preferred language
3. Select "Real Order" as the data source
4. Enter a valid Order ID in the input field that appears
5. Click "Generate Preview"
6. View the rendered email with actual order data

**Note**: If the Order ID doesn't exist, the system will show an error message.

## Common Use Cases

### Testing a New Template

1. Select the template you want to test
2. Use "Sample Data" to quickly see how it looks
3. Test all three languages (Greek, English, German)
4. Check both HTML and Text versions
5. Verify all information displays correctly

### Verifying Order Email

1. Find the Order ID from the Orders section
2. Select the appropriate template (e.g., "Order Shipped")
3. Choose "Real Order" and enter the Order ID
4. Generate preview to see exactly what the customer will receive
5. Verify all order details are correct

### Checking Template Translations

1. Select any template
2. Generate preview in Greek
3. Switch language to English and generate again
4. Switch to German and generate again
5. Compare to ensure all translations are present and correct

## Tips and Best Practices

### ✅ Do's

- **Always test with sample data first** before using real orders
- **Check all three languages** to ensure translations are complete
- **Verify both HTML and text versions** - some email clients only show text
- **Test different order statuses** to see how templates adapt
- **Use real order IDs** when you need to verify specific customer data

### ❌ Don'ts

- **Don't use customer Order IDs in production** without permission
- **Don't rely only on HTML preview** - always check text version
- **Don't forget to test edge cases** (long names, many items, etc.)
- **Don't modify templates directly** without testing first

## Troubleshooting

### "Template not found" Error

**Problem**: Selected template cannot be loaded

**Solution**:
- Refresh the page
- Check that the template file exists in `core/templates/emails/`
- Contact development team if issue persists

### "Order not found" Error

**Problem**: Entered Order ID doesn't exist

**Solution**:
- Verify the Order ID is correct
- Check the Orders section to find valid Order IDs
- Use "Sample Data" instead for testing

### Preview Shows Blank Content

**Problem**: Preview area is empty after clicking "Generate Preview"

**Solution**:
- Check browser console for JavaScript errors
- Ensure you selected a template first
- Try refreshing the page
- Clear browser cache

### Translations Missing

**Problem**: Some text appears in wrong language or untranslated

**Solution**:
- This indicates missing translations in the template
- Report to development team with:
  - Template name
  - Language where translation is missing
  - Specific text that's not translated

## Understanding Template Status

### Active Templates

These templates are currently being used by the system:
- Order Confirmation (sent when order is created)
- Order Shipped (sent when order ships)
- Order Delivered (sent when order is delivered)
- Order Canceled (sent when order is canceled)
- Order Pending Reminder (sent after 24 hours if still pending)
- Order Status Generic (used for various status updates)

### Unused Templates

These templates are available but not actively triggered:
- Order Completed
- Order Pending
- Order Processing
- Order Refunded
- Order Returned

**Note**: Unused templates can still be previewed and are ready to use if needed.

## Sample Data Details

When using "Sample Data", the system generates:
- Random Greek customer names and addresses
- Realistic order numbers and dates
- Sample products with prices
- Appropriate order status
- Tracking numbers (for shipping templates)
- Calculated totals and shipping costs

This data is **not saved** to the database - it's only for preview purposes.

## Real Order Data Details

When using "Real Order", the system loads:
- Actual customer information
- Real order items and prices
- Current order status
- Actual tracking numbers (if available)
- Real timestamps and dates

**Privacy Note**: Be careful when previewing real orders - they contain actual customer data.

## Keyboard Shortcuts

- **Tab**: Navigate between controls
- **Enter**: Generate preview (when button is focused)
- **Esc**: Close any error messages

## Getting Help

If you encounter issues:

1. **Check this guide** for common solutions
2. **Review error messages** carefully - they often explain the problem
3. **Try with sample data** to isolate the issue
4. **Contact development team** with:
   - What you were trying to do
   - Error message (if any)
   - Template name and language
   - Order ID (if using real data)

## Best Practices for Email Testing

### Before Sending to Customers

1. ✅ Preview template with sample data
2. ✅ Check all three languages
3. ✅ Verify HTML and text versions
4. ✅ Test with real order (if possible)
5. ✅ Check on mobile preview (if available)
6. ✅ Verify all links work
7. ✅ Confirm branding is consistent

### Regular Maintenance

- **Weekly**: Spot-check active templates
- **Monthly**: Review all templates for accuracy
- **After updates**: Test affected templates immediately
- **Before holidays**: Verify templates with seasonal content

## Advanced Features

### Template Categories

Templates are organized by their purpose:
- **Order Lifecycle**: Status changes throughout order process
- **Shipping**: Delivery and tracking updates

### Multi-language Support

All templates support three languages:
- **Greek (el)**: Primary language
- **English (en)**: International customers
- **German (de)**: European customers

The system automatically uses the customer's preferred language when sending emails.

### Brand Consistency

All templates use the GrooveShop brand design:
- Consistent colors and fonts
- Professional layout
- Mobile-responsive design
- Accessible to all users

## FAQ

**Q: Can I edit templates from the admin interface?**
A: No, templates must be edited in the code. This interface is for preview and testing only.

**Q: How do I know which template is used for which order status?**
A: Check the template description and the "Active" badge. Active templates are automatically sent.

**Q: Can I send a test email to myself?**
A: Not directly from this interface. Contact development team for test email functionality.

**Q: What happens if I enter an invalid Order ID?**
A: The system will show an error message and you can try again with a different ID.

**Q: Why do some templates show "Unused"?**
A: These templates are available but not currently triggered by the system. They may be used in the future.

**Q: Can I preview templates for other types of emails (not orders)?**
A: Currently, only order-related templates are available. Other email types may be added in the future.

## Support

For technical support or questions:
- Email: dev@grooveshop.com
- Check logs: `logs/grooveshop_dev.log`
- Review main documentation: `core/email/README.md`

---

**Last Updated**: November 2025
**Version**: 1.0
