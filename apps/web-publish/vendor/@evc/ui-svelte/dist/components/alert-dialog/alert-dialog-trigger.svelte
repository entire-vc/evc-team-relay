<script lang="ts">
  import { AlertDialog as AlertDialogPrimitive } from 'bits-ui';

  type Props = AlertDialogPrimitive.TriggerProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<AlertDialogPrimitive.Trigger {...restProps}>
  {@render children?.()}
</AlertDialogPrimitive.Trigger>
