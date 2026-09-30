# A small Windows Forms sign-up window with the same planted accessibility
# bugs as examples/signup.html. It only needs the PowerShell that ships with
# Windows:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File examples\windows\signup_form.ps1
#
# Planted bugs:
#   1. The "Library card number" box has visible text next to it but no
#      AccessibleName, so screen readers just say "edit".
#   2. The help button shows only an icon and has no AccessibleName.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Sign up - Example Library'
$form.ClientSize = New-Object System.Drawing.Size(420, 230)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

function New-Control($type, $x, $y, $width, $tabIndex) {
    $c = New-Object "System.Windows.Forms.$type"
    $c.Location = New-Object System.Drawing.Point($x, $y)
    if ($width) { $c.Width = $width }
    $c.TabIndex = $tabIndex
    $form.Controls.Add($c)
    return $c
}

$lblName = New-Control 'Label' 20 21 130 0
$lblName.Text = 'Full name'
$txtName = New-Control 'TextBox' 160 18 230 1
$txtName.AccessibleName = 'Full name'

$lblEmail = New-Control 'Label' 20 56 130 2
$lblEmail.Text = 'Email'
$txtEmail = New-Control 'TextBox' 160 53 230 3
$txtEmail.AccessibleName = 'Email'

# BUG 1: the label is not linked to the box (no AccessibleName, and the label
# is not just before the box in tab order, which WinForms would use).
$lblCard = New-Control 'Label' 20 91 130 20
$lblCard.Text = 'Library card number'
$txtCard = New-Control 'TextBox' 160 88 230 4

$chkTerms = New-Control 'CheckBox' 20 125 300 5
$chkTerms.Text = 'I agree to the terms'

$btnCreate = New-Control 'Button' 20 170 140 6
$btnCreate.Text = 'Create account'
$btnCreate.Height = 32
$form.AcceptButton = $btnCreate

# BUG 2: icon-only button without an AccessibleName.
$btnHelp = New-Control 'Button' 170 170 32 7
$btnHelp.Height = 32
$btnHelp.Text = ''
$btnHelp.Image = [System.Drawing.SystemIcons]::Question.ToBitmap()

$btnCreate.Add_Click({
    $errors = @()
    if (-not $txtName.Text.Trim()) { $errors += 'Enter your full name.' }
    if ($txtEmail.Text -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { $errors += 'Enter a valid email address.' }
    if (-not $chkTerms.Checked) { $errors += 'You must agree to the terms.' }

    if ($errors.Count -gt 0) {
        [void][System.Windows.Forms.MessageBox]::Show($form, ($errors -join ' '), 'Error',
            [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
    } else {
        $form.Controls.Clear()
        $form.Text = 'Welcome - Example Library'
        $done = New-Object System.Windows.Forms.Label
        $done.Text = 'Welcome! Your account is ready.'
        $done.AutoSize = $true
        $done.Location = New-Object System.Drawing.Point(20, 20)
        $form.Controls.Add($done)
        $close = New-Object System.Windows.Forms.Button
        $close.Text = 'Close'
        $close.Location = New-Object System.Drawing.Point(20, 60)
        $close.Add_Click({ $form.Close() })
        $form.Controls.Add($close)
        $close.Focus()
    }
})

$btnHelp.Add_Click({
    [void][System.Windows.Forms.MessageBox]::Show($form, 'Ask at the front desk.', 'Help')
})

[void]$form.ShowDialog()
